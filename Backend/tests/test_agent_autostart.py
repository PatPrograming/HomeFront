"""The console starts the chat agent by itself (HOMEFRONT_AUTOSTART_AGENT=1).

A fake launcher stands in for the agent process: no real process, no AWS, no Bedrock.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest
from mandate_contracts.ddb import SessionItem

from app.main import app
from app.services import agent_supervisor
from app.services.agent_supervisor import AgentSupervisor, agent_command, child_env

from .test_assistant_api import SID, as_staff, session  # noqa: F401 (fixtures)
from .test_gateway_api import _env, as_admin, client, gateway, store, table  # noqa: F401 (fixtures)

URL = f"/api/v1/sessions/{SID}/messages"


class FakeProc:
    """Looks like subprocess.Popen to the supervisor."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def crash(self, code: int = 1):
        self.returncode = code

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class FakeLauncher:
    def __init__(self, fail: Exception | None = None):
        self.started: list[tuple[str, FakeProc]] = []
        self.fail = fail

    def __call__(self, sid: str) -> FakeProc:
        if self.fail:
            raise self.fail
        proc = FakeProc(1000 + len(self.started))
        self.started.append((sid, proc))
        return proc


@pytest.fixture
def launcher():
    fake = FakeLauncher()
    agent_supervisor.set_supervisor(AgentSupervisor(fake, max_starts=3))
    yield fake
    agent_supervisor.set_supervisor(None)


def send(client, text="Check my email"):
    as_staff()
    return client.post(URL, json={"text": text})


# --- starting ---------------------------------------------------------------------


def test_the_first_message_starts_exactly_one_agent(client, session, launcher):
    r = send(client)
    assert r.status_code == 200
    assert [sid for sid, _ in launcher.started] == [SID]


def test_a_second_message_does_not_start_another(client, session, launcher):
    send(client)
    r = send(client, "And the newsletter?")
    assert r.status_code == 200
    assert len(launcher.started) == 1


def test_the_browser_gets_the_same_chat_message_as_before(client, session, launcher):
    body = send(client).json()
    assert set(body) == {"id", "ts", "role", "text"} and body["text"] == "Check my email"


# --- stopping ---------------------------------------------------------------------


def test_offboarding_stops_the_agent(client, session, launcher):
    send(client)
    as_admin()
    assert client.post("/api/v1/staff/maya/offboard").status_code == 200
    assert launcher.started[0][1].terminated


def test_a_revoked_session_is_stopped_by_the_sweep(client, session, store, launcher):
    send(client)
    revoked = store.get_session(SID).model_copy(update={"status": "revoked"})
    store.put_session(SID, revoked)

    agent_supervisor.sweep_once()

    assert launcher.started[0][1].terminated


def test_an_expired_session_is_stopped_by_the_sweep(client, session, store, launcher):
    send(client)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.put_session(SID, store.get_session(SID).model_copy(update={"created_at": old}))

    agent_supervisor.sweep_once()

    assert launcher.started[0][1].terminated


def test_a_live_session_is_left_running_by_the_sweep(client, session, launcher):
    send(client)
    agent_supervisor.sweep_once()
    assert not launcher.started[0][1].terminated


def test_no_agent_is_started_for_an_expired_session(client, store, launcher):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.put_session(SID, SessionItem(status="active", agent="agent-1", acting_for="maya", office="rep-x",
                                       mode="official", created_at=old))
    r = send(client)
    assert r.status_code == 503 and "expired" in r.json()["detail"]
    assert launcher.started == []


def test_server_shutdown_stops_every_agent(client, session, launcher):
    send(client)

    async def run():
        async with agent_supervisor.supervision(sweep_seconds=3600):
            pass

    asyncio.run(run())
    assert launcher.started[0][1].terminated


# --- crashes and the restart cap ------------------------------------------------------


def test_a_crashed_agent_is_restarted_by_the_next_message(client, session, launcher):
    send(client)
    launcher.started[0][1].crash()
    r = send(client, "Are you there?")
    assert r.status_code == 200
    assert len(launcher.started) == 2


def test_restarts_stop_after_the_cap(client, session, store, launcher):
    for _ in range(3):
        send(client)
        launcher.started[-1][1].crash()
    messages_before = len(store.chat_since(SID, None)[0])

    r = send(client, "Hello?")

    assert r.status_code == 503 and "3" in r.json()["detail"]
    assert len(launcher.started) == 3
    assert len(store.chat_since(SID, None)[0]) == messages_before, "a refused message is not stored"


def test_an_agent_that_cannot_start_is_a_clear_error_not_a_hang(client, session, store):
    agent_supervisor.set_supervisor(AgentSupervisor(FakeLauncher(fail=FileNotFoundError("agent/.venv/bin/python"))))
    try:
        r = send(client)
    finally:
        agent_supervisor.set_supervisor(None)
    assert r.status_code == 503
    assert r.json()["detail"].startswith("The assistant could not start")
    assert store.chat_since(SID, None)[0] == []


# --- off by default ---------------------------------------------------------------------


@pytest.mark.parametrize(("flag", "launches"), [("0", 0), ("1", 1)])  # "1" proves the counter can see a launch
def test_disabled_starts_nothing(client, session, monkeypatch, flag, launches):
    calls = []
    monkeypatch.setattr(agent_supervisor, "launch", lambda sid: calls.append(sid) or FakeProc(1))
    monkeypatch.setenv("HOMEFRONT_AUTOSTART_AGENT", flag)
    monkeypatch.setattr(agent_supervisor, "_configured", False)  # read the flag again, as a fresh server would
    r = send(client)
    assert r.status_code == 200 and len(calls) == launches


@pytest.mark.parametrize(("value", "enabled"), [(None, False), ("", False), ("0", False), ("1", True), ("true", True)])
def test_the_env_flag_turns_it_on(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("HOMEFRONT_AUTOSTART_AGENT", raising=False)
    else:
        monkeypatch.setenv("HOMEFRONT_AUTOSTART_AGENT", value)
    assert (agent_supervisor.from_env() is not None) is enabled


# --- what the child process gets --------------------------------------------------------


def test_the_agent_command_is_the_agents_own_python_and_script():
    argv, cwd = agent_command("sid-9")
    assert argv[0].endswith("agent/.venv/bin/python")
    assert argv[1:] == ["agent/agent.py", "--sid", "sid-9", "--chat"]
    assert (cwd / "agent" / "agent.py").exists()


def test_the_child_never_gets_the_signing_secret_or_any_other_server_secret():
    parent = {
        "PATH": "/usr/bin",
        "SSL_CERT_FILE": "/certifi/cacert.pem",
        "PYTHONPATH": "Backend:packages/identity-gateway/src",
        "HOME": "/Users/x",
        "AWS_ACCESS_KEY_ID": "AKIA-test",
        "AWS_SECRET_ACCESS_KEY": "s",
        "AWS_SESSION_TOKEN": "t",
        "AWS_REGION": "us-east-1",
        "MANDATE_SIGNING_SECRET": "never-to-the-agent",
        "COGNITO_AGENT_CLIENT_SECRET": "the-agent-reads-its-own-file",
        "DATA_GOV_API_KEY": "gateway-held",
        "AWS_BEARER_TOKEN_BEDROCK": "teammates-runner",
        "AVP_POLICY_STORE_ID": "ps-1",
    }
    env = child_env(parent)
    assert {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION", "PATH", "SSL_CERT_FILE"} <= env.keys()
    for secret in ("MANDATE_SIGNING_SECRET", "COGNITO_AGENT_CLIENT_SECRET", "DATA_GOV_API_KEY",
                   "AWS_BEARER_TOKEN_BEDROCK", "AVP_POLICY_STORE_ID", "PYTHONPATH"):
        assert secret not in env


def test_start_and_stop_are_logged(client, session, launcher, caplog):
    caplog.set_level(logging.INFO, logger="homefront.agent_supervisor")
    send(client)
    as_admin()
    client.post("/api/v1/staff/maya/offboard")
    lines = [r.getMessage() for r in caplog.records if r.name == "homefront.agent_supervisor"]
    assert any(line.startswith(f"agent started sid={SID}") for line in lines), lines
    assert any(line.startswith(f"agent stopped sid={SID}") and "offboarded" in line for line in lines), lines


def test_stopping_the_real_app_stops_its_agents(gateway, session, launcher):
    from fastapi.testclient import TestClient

    app.dependency_overrides.clear()
    with TestClient(app) as running:  # runs the app's own lifespan: startup, then shutdown on exit
        send(running)
        assert not launcher.started[0][1].terminated
    app.dependency_overrides.clear()
    assert launcher.started[0][1].terminated


def test_start_and_stop_reach_the_servers_terminal(capfd):
    """No caplog: the lines must be printed with the server's own logging setup."""
    fake = FakeLauncher()
    agent_supervisor.set_supervisor(AgentSupervisor(fake))

    async def run():
        async with agent_supervisor.supervision(sweep_seconds=3600):
            agent_supervisor.get_supervisor().ensure_running("sid-log", "maya", ends_at=9e9)

    try:
        asyncio.run(run())
    finally:
        agent_supervisor.set_supervisor(None)
    err = capfd.readouterr().err
    assert "agent started sid=sid-log" in err and "agent stopped sid=sid-log" in err and "server shutdown" in err
