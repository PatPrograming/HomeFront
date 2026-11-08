"""When the chat agent can't answer, the staffer reads why in the chat, and can recover.

moto DynamoDB and a fake launcher: no real process, no AWS, no Bedrock.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from app.services import agent_failures, agent_supervisor
from app.services.agent_supervisor import AgentSupervisor

from .test_agent_autostart import FakeLauncher
from .test_assistant_api import agent_headers
from .test_conversations import DANA, login, open_conversation
from .test_gateway_api import CHIEF, _env, client, gateway, store, table  # noqa: F401 (fixtures)

AGENT_CHAT = Path(__file__).resolve().parents[2] / "agent" / "mandate_agent" / "chat.py"


@pytest.fixture
def launcher():
    fake = FakeLauncher()
    agent_supervisor.set_supervisor(AgentSupervisor(fake, on_exit=agent_failures.report_exit))
    yield fake
    agent_supervisor.set_supervisor(None)


def chat(client, sid, who=None):
    login(who) if who else login()
    return client.get(f"/api/v1/sessions/{sid}/messages").json()["messages"]


def say(client, sid, text="hello", who=None):
    login(who) if who else login()
    return client.post(f"/api/v1/sessions/{sid}/messages", json={"text": text})


def test_the_exit_codes_match_the_agents():
    source = AGENT_CHAT.read_text() + (AGENT_CHAT.parent / "runner.py").read_text()
    agent_codes = {name: int(v) for name, v in re.findall(r"^(EXIT_[A-Z_]+) = (\d+)", source, re.M)}
    for name, value in agent_failures.EXIT_CODES.items():
        assert agent_codes[name] == value, name


# --- what the staffer reads -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "words"),
    [
        (4, "The assistant isn't set up on this computer (agent secret missing). Ask your admin."),
        (7, "couldn't sign in"),
        (5, "couldn't reach its sign-in service"),
        (1, "stopped unexpectedly"),
        (-9, "stopped unexpectedly"),
    ],
)
def test_an_agent_that_dies_without_posting_is_explained_in_the_chat(client, launcher, code, words):
    sid = open_conversation(client, "check my email")
    launcher.started[0][1].crash(code)
    agent_supervisor.sweep_once()
    replies = [m for m in chat(client, sid) if m["role"] == "assistant"]
    assert len(replies) == 1 and words in replies[0]["text"]


@pytest.mark.parametrize("code", [3, 6, 0])
def test_no_extra_message_when_the_agent_already_said_why_or_the_session_ended(client, launcher, code):
    sid = open_conversation(client, "check my email")
    launcher.started[0][1].crash(code)
    agent_supervisor.sweep_once()
    assert [m for m in chat(client, sid) if m["role"] == "assistant"] == []


def test_an_exit_is_reported_once(client, launcher):
    sid = open_conversation(client, "check my email")
    launcher.started[0][1].crash(4)
    agent_supervisor.sweep_once()
    agent_supervisor.sweep_once()
    assert len([m for m in chat(client, sid) if m["role"] == "assistant"]) == 1


def test_the_gateway_terminal_says_exactly_what_to_fix(client, launcher, caplog):
    caplog.set_level(logging.WARNING, logger="homefront.agent_supervisor")
    open_conversation(client, "check my email")
    launcher.started[0][1].crash(4)
    agent_supervisor.sweep_once()
    assert any("COGNITO_AGENT_CLIENT_SECRET" in r.getMessage() and "agent/.env" in r.getMessage() for r in caplog.records)


def test_a_missing_secret_is_not_restarted_until_someone_retries(client, launcher):
    sid = open_conversation(client, "check my email")
    launcher.started[0][1].crash(4)
    r = say(client, sid, "are you there?")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "agent_not_set_up"
    assert "agent secret missing" in r.json()["detail"]["message"]
    assert len(launcher.started) == 1


# --- admins see the technical detail, staffers don't --------------------------------------------


def test_the_error_class_is_shown_to_admins_only(client, store):
    staff_sid = open_conversation(client, "check my email")
    admin_sid = open_conversation(client, "check my email", who=CHIEF)
    for sid, act in ((staff_sid, "maya"), (admin_sid, "chiefofstaff")):
        headers = agent_headers(sid) if act == "maya" else _admin_agent_headers(sid)
        r = client.post("/api/v1/agent/messages", json={"text": "I stopped: the AI model is busy right now.",
                                                       "detail": "ClientError: ThrottlingException"}, headers=headers)
        assert r.status_code == 200, r.text

    staff_reply = [m for m in chat(client, staff_sid) if m["role"] == "assistant"][0]
    admin_reply = [m for m in chat(client, admin_sid, who=CHIEF) if m["role"] == "assistant"][0]
    assert "detail" not in staff_reply
    assert admin_reply["detail"] == "ClientError: ThrottlingException"


def _admin_agent_headers(sid):
    import time

    from identity_gateway.tokens import mint_mandate_token
    from mandate_contracts import MandateClaims

    from .test_gateway_api import SECRET

    now = int(time.time())
    claims = MandateClaims(sub="agent-1", act="chiefofstaff", office="rep-x", mode="official", sid=sid, iat=now, exp=now + 900)
    return {"Authorization": f"Bearer {mint_mandate_token(claims, SECRET)}"}


def test_a_detail_is_short_and_needs_the_agents_token(client):
    sid = open_conversation(client, "check my email")
    assert client.post("/api/v1/agent/messages", json={"text": "x", "detail": "d"}).status_code == 401
    r = client.post("/api/v1/agent/messages", json={"text": "x", "detail": "d" * 301}, headers=agent_headers(sid))
    assert r.status_code == 422


# --- 3. the start cap: Try again, or a new conversation -----------------------------------------


def _hit_the_cap(client, launcher, sid):
    for _ in range(3):
        launcher.started[-1][1].crash(6)
        say(client, sid, "again")
    return say(client, sid, "and again")


def test_the_cap_offers_try_again_and_a_new_conversation(client, launcher):
    sid = open_conversation(client, "check my email")
    r = _hit_the_cap(client, launcher, sid)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["code"] == "agent_start_cap" and detail["actions"] == ["retry", "new_conversation"]
    assert "Try again" in detail["message"] and "new conversation" in detail["message"]


def test_try_again_resets_the_count_and_starts_the_agent(client, launcher, caplog):
    caplog.set_level(logging.INFO, logger="homefront.agent_supervisor")
    sid = open_conversation(client, "check my email")
    _hit_the_cap(client, launcher, sid)
    started = len(launcher.started)

    login()
    r = client.post(f"/api/v1/sessions/{sid}/agent/retry")

    assert r.status_code == 200 and r.json() == {"sid": sid, "agent": "started", "retriesLeft": 2}
    assert len(launcher.started) == started + 1
    assert any(r.getMessage().startswith(f"audit: agent retry sid={sid} by=maya") for r in caplog.records)


def test_try_again_is_rate_limited(client, launcher):
    sid = open_conversation(client, "check my email")
    _hit_the_cap(client, launcher, sid)
    login()
    assert client.post(f"/api/v1/sessions/{sid}/agent/retry").status_code == 200
    launcher.started[-1][1].crash(6)
    r = client.post(f"/api/v1/sessions/{sid}/agent/retry")
    assert r.status_code == 429 and "wait" in r.json()["detail"].lower()


def test_try_again_has_a_limit_per_conversation(client, launcher, monkeypatch):
    sid = open_conversation(client, "check my email")
    clock = [1_000_000.0]
    monkeypatch.setattr(agent_supervisor.time, "time", lambda: clock[0])
    login()
    codes = []
    for _ in range(4):
        launcher.started[-1][1].crash(6)
        clock[0] += 120
        codes.append(client.post(f"/api/v1/sessions/{sid}/agent/retry").status_code)
    assert codes == [200, 200, 200, 429]


def test_try_again_clears_a_missing_secret_once_it_is_fixed(client, launcher):
    sid = open_conversation(client, "check my email")
    launcher.started[0][1].crash(4)
    agent_supervisor.sweep_once()
    login()
    assert client.post(f"/api/v1/sessions/{sid}/agent/retry").status_code == 200
    assert len(launcher.started) == 2


def test_try_again_is_owner_only(client, launcher):
    sid = open_conversation(client, "check my email")
    login(DANA)
    assert client.post(f"/api/v1/sessions/{sid}/agent/retry").status_code == 404
    login(CHIEF)
    assert client.post(f"/api/v1/sessions/{sid}/agent/retry").status_code == 404


def test_try_again_on_a_revoked_session_is_refused(client, launcher, store):
    sid = open_conversation(client, "check my email")
    store.put_session(sid, store.get_session(sid).model_copy(update={"status": "revoked"}))
    login()
    assert client.post(f"/api/v1/sessions/{sid}/agent/retry").status_code == 403


def test_try_again_without_auto_start_says_so(client):
    sid = open_conversation(client, "check my email")
    login()
    r = client.post(f"/api/v1/sessions/{sid}/agent/retry")
    assert r.status_code == 409 and "auto-start" in r.json()["detail"]


# --- found by the trial: the server's own supervisor must explain exits too --------------------


def test_the_supervisor_the_server_builds_explains_exits_in_the_chat(client, monkeypatch):
    monkeypatch.setenv("HOMEFRONT_AUTOSTART_AGENT", "1")
    built = agent_supervisor.from_env()
    fake = FakeLauncher()
    built._launch = fake  # the real launcher would start a process
    agent_supervisor.set_supervisor(built)
    try:
        sid = open_conversation(client, "check my email")
        fake.started[0][1].crash(4)
        agent_supervisor.sweep_once()
    finally:
        agent_supervisor.set_supervisor(None)
    assert any("agent secret missing" in m["text"] for m in chat(client, sid) if m["role"] == "assistant")


def test_an_unexplained_failure_is_explained_by_the_server(client, launcher):
    sid = open_conversation(client, "check my email")
    launcher.started[0][1].crash(8)
    agent_supervisor.sweep_once()
    replies = [m["text"] for m in chat(client, sid) if m["role"] == "assistant"]
    assert len(replies) == 1 and "couldn't say why" in replies[0]
