"""The chat agent gets Bedrock-only credentials, never the server's own.

With HOMEFRONT_AGENT_ROLE_ARN set, the supervisor assumes that role per conversation and the agent process gets
only those temporary credentials. Without it (a laptop), the old behaviour stays, with a loud warning. A fake STS
client and a fake Popen: no AWS call, no real process.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import pytest

from app.main import app
from app.services import agent_supervisor
from app.services.agent_supervisor import AgentStartError, assume_agent_role, child_env

from .test_conversations import login, open_conversation
from .test_gateway_api import CHIEF, MAYA, _env, client, gateway, store, table  # noqa: F401 (fixtures)

ROLE = "arn:aws:iam::123456789012:role/homefront-agent"
SERVER = {
    "PATH": "/usr/bin",
    "HOME": "/home/gw",
    "AWS_REGION": "us-east-1",
    "AWS_ACCESS_KEY_ID": "AKIASERVERKEY",
    "AWS_SECRET_ACCESS_KEY": "server-secret",
    "AWS_SESSION_TOKEN": "server-token",
    "AWS_PROFILE": "server-profile",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/gateway-task",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://169.254.170.2/creds",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN": "container-token",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE": "/var/run/token",
    "MANDATE_SIGNING_SECRET": "never",
}
SERVER_SECRETS = ("AKIASERVERKEY", "server-secret", "server-token", "server-profile", "gateway-task",
                  "container-token", "/var/run/token", "169.254.170.2")


class FakeSTS:
    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    def assume_role(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"Credentials": {"AccessKeyId": "ASIAAGENT", "SecretAccessKey": "agent-secret", "SessionToken": "agent-token",
                                "Expiration": "2026-11-08T13:00:00Z"}}


# --- what the agent process receives ---------------------------------------------------------


def test_with_a_role_the_child_gets_only_the_roles_temporary_credentials():
    creds = assume_agent_role("sid-1", ROLE, FakeSTS())
    env = child_env(SERVER, creds)
    assert (env["AWS_ACCESS_KEY_ID"], env["AWS_SECRET_ACCESS_KEY"], env["AWS_SESSION_TOKEN"]) == (
        "ASIAAGENT", "agent-secret", "agent-token")
    blob = json.dumps(env)
    for secret in SERVER_SECRETS:
        assert secret not in blob, secret
    # No other source to fall back to: no profile files, no instance metadata.
    assert env["AWS_CONFIG_FILE"] == env["AWS_SHARED_CREDENTIALS_FILE"] == "/dev/null"
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert env["AWS_REGION"] == "us-east-1" and env["PATH"] == "/usr/bin"


def test_the_container_credentials_endpoint_never_reaches_the_child_either_way():
    for env in (child_env(SERVER), child_env(SERVER, assume_agent_role("s", ROLE, FakeSTS()))):
        assert not any(key.startswith("AWS_CONTAINER_") for key in env)


def test_the_role_session_is_named_for_the_conversation_lasts_an_hour_and_is_bedrock_only():
    sts = FakeSTS()
    assume_agent_role("sid-42", ROLE, sts)
    (call,) = sts.calls
    assert call["RoleArn"] == ROLE and call["RoleSessionName"] == "agent-sid-42" and call["DurationSeconds"] == 3600
    policy = json.loads(call["Policy"])
    assert policy["Statement"] == [  # the whole list, so an extra or widened statement fails too
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:us-east-1:*:inference-profile/us.anthropic.claude-sonnet-5*",
                      "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5*"]},
        {"Effect": "Allow", "Action": ["bedrock:ApplyGuardrail"],
         "Resource": ["arn:aws:bedrock:us-east-1:*:guardrail/l74ju5pcrcdg"]},
    ]


def test_a_long_session_id_still_makes_a_valid_session_name():
    sts = FakeSTS()
    assume_agent_role("x" * 100, ROLE, sts)
    name = sts.calls[0]["RoleSessionName"]
    assert len(name) <= 64 and name.startswith("agent-")


def test_without_a_role_today_s_behaviour_stays_for_laptops():
    env = child_env(SERVER)
    assert env["AWS_ACCESS_KEY_ID"] == "AKIASERVERKEY"


# --- launching ------------------------------------------------------------------------------


@pytest.fixture
def agent_files(tmp_path, monkeypatch):
    python = tmp_path / "python"
    python.write_text("")
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setattr(agent_supervisor, "AGENT_PYTHON", python)
    monkeypatch.setattr(agent_supervisor, "AGENT_ENV_FILE", env_file)
    started = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, cwd, env: started.append(env) or object())
    for key, value in SERVER.items():
        monkeypatch.setenv(key, value)
    return started


def test_launch_assumes_the_role_and_hands_over_nothing_else(agent_files, monkeypatch):
    sts = FakeSTS()
    monkeypatch.setenv("HOMEFRONT_AGENT_ROLE_ARN", ROLE)
    monkeypatch.setattr(agent_supervisor, "_sts_client", lambda: sts)
    agent_supervisor.launch("sid-7")
    (env,) = agent_files
    assert env["AWS_ACCESS_KEY_ID"] == "ASIAAGENT" and sts.calls[0]["RoleSessionName"] == "agent-sid-7"
    assert not any(secret in json.dumps(env) for secret in SERVER_SECRETS)


def test_if_the_role_cannot_be_assumed_the_agent_does_not_start(agent_files, monkeypatch):
    from botocore.exceptions import ClientError

    monkeypatch.setenv("HOMEFRONT_AGENT_ROLE_ARN", ROLE)
    denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "not authorized AKIA..."}}, "AssumeRole")
    monkeypatch.setattr(agent_supervisor, "_sts_client", lambda: FakeSTS(denied))
    with pytest.raises(AgentStartError) as raised:
        agent_supervisor.launch("sid-7")
    assert agent_files == [], "fail closed: no process with the server's credentials instead"
    assert raised.value.code == "agent_role_failed"
    assert "limited AWS access" in str(raised.value) and "AKIA" not in str(raised.value)


def test_the_staffer_reads_why_in_the_chat_when_the_role_fails(client, agent_files, monkeypatch):
    from botocore.exceptions import NoCredentialsError

    monkeypatch.setenv("HOMEFRONT_AGENT_ROLE_ARN", ROLE)
    monkeypatch.setattr(agent_supervisor, "_sts_client", lambda: FakeSTS(NoCredentialsError()))
    agent_supervisor.set_supervisor(agent_supervisor.AgentSupervisor(agent_supervisor.launch))
    try:
        sid = open_conversation(client)
        login()
        r = client.post(f"/api/v1/sessions/{sid}/messages", json={"text": "check my email"})
    finally:
        agent_supervisor.set_supervisor(None)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "agent_role_failed"
    assert "Ask your admin" in r.json()["detail"]["message"]


# --- telling admins when the agent shares the server's credentials ------------------------------


def test_a_loud_warning_at_startup_when_the_agent_shares_the_servers_credentials(monkeypatch, caplog):
    import asyncio

    monkeypatch.delenv("HOMEFRONT_AGENT_ROLE_ARN", raising=False)
    caplog.set_level(logging.WARNING, logger="homefront.agent_supervisor")
    agent_supervisor.set_supervisor(agent_supervisor.AgentSupervisor(lambda sid: None))

    async def run():
        async with agent_supervisor.supervision(sweep_seconds=3600):
            pass

    try:
        asyncio.run(run())
    finally:
        agent_supervisor.set_supervisor(None)
    assert any("shares the server's AWS credentials" in r.getMessage() and "HOMEFRONT_AGENT_ROLE_ARN" in r.getMessage()
               for r in caplog.records)


def test_no_warning_when_the_role_is_set(monkeypatch, caplog):
    import asyncio

    monkeypatch.setenv("HOMEFRONT_AGENT_ROLE_ARN", ROLE)
    caplog.set_level(logging.WARNING, logger="homefront.agent_supervisor")
    agent_supervisor.set_supervisor(agent_supervisor.AgentSupervisor(lambda sid: None))

    async def run():
        async with agent_supervisor.supervision(sweep_seconds=3600):
            pass

    try:
        asyncio.run(run())
    finally:
        agent_supervisor.set_supervisor(None)
    assert not any("shares the server's AWS credentials" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(("role", "mode"), [(None, "shared-server"), (ROLE, "limited-role")])
def test_admins_see_which_credentials_the_agent_uses(client, monkeypatch, role, mode):
    from app.routers import assistant

    if role:
        monkeypatch.setenv("HOMEFRONT_AGENT_ROLE_ARN", role)
    else:
        monkeypatch.delenv("HOMEFRONT_AGENT_ROLE_ARN", raising=False)
    app.dependency_overrides[assistant.require_admin] = lambda: CHIEF
    body = client.get("/api/v1/assistant/status").json()
    assert body["agentCredentials"] == mode


def test_staffers_cannot_read_the_assistant_status(client, monkeypatch):
    from fastapi import HTTPException

    from app.routers import assistant

    def not_admin():
        raise HTTPException(403, "Admins only")

    app.dependency_overrides[assistant.require_admin] = not_admin
    assert client.get("/api/v1/assistant/status").status_code == 403
    app.dependency_overrides.clear()
    assert client.get("/api/v1/assistant/status").status_code == 401


SCRIPT = Path(__file__).resolve().parents[1] / "infra" / "agent-role.sh"


def _dry_run(tmp_path, *args, exit_code=1, get_role_exit=None):
    """Run the script with a fake `aws` that records every call (a dry run must make none)."""
    calls = tmp_path / "aws-calls"
    fake = tmp_path / "bin"
    fake.mkdir()
    get_role = f'case "$1 $2" in "iam get-role") exit {get_role_exit};; esac\n' if get_role_exit is not None else ""
    (fake / "aws").write_text(f'#!/bin/sh\necho "$@" >> {calls}\n{get_role}exit {exit_code}\n')
    (fake / "aws").chmod(0o755)
    env = {"PATH": f"{fake}:/usr/bin:/bin", "AWS_ACCOUNT_ID": "123456789012"}
    result = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env)
    return result, calls


def test_the_role_script_is_a_dry_run_by_default_and_calls_nothing(tmp_path):
    result, calls = _dry_run(tmp_path, "--trust-principal", "arn:aws:iam::123456789012:role/Admin")
    assert result.returncode == 0, result.stderr
    assert not calls.exists(), "a dry run must not call AWS"
    out = result.stdout
    trust = json.loads(out.split("--- trust policy ---")[1].split("---")[0])
    (statement,) = trust["Statement"]
    assert statement["Action"] == "sts:AssumeRole"
    assert statement["Condition"]["ArnEquals"]["aws:PrincipalArn"] == [
        "arn:aws:iam::123456789012:role/homefront-gateway-task", "arn:aws:iam::123456789012:role/Admin"]
    permissions = json.loads(out.split("--- permissions (inline policy homefront-agent-bedrock) ---")[1].split("+ aws")[0])
    actions = {a for s in permissions["Statement"] for a in s["Action"]}
    assert actions == {"bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock:ApplyGuardrail"}
    assert "Key=project,Value=homefront" in out and "--max-session-duration 3600" in out


def test_the_role_script_refuses_an_assumed_role_session_as_principal(tmp_path):
    result, calls = _dry_run(tmp_path, "--trust-principal", "arn:aws:sts::123456789012:assumed-role/Admin/me")
    assert result.returncode == 2 and "role ARN" in result.stderr and not calls.exists()


def test_apply_on_an_existing_role_also_tags_it(tmp_path):
    result, calls = _dry_run(tmp_path, "--apply", exit_code=0)  # every fake call succeeds, so get-role finds the role
    assert result.returncode == 0, result.stderr
    made = calls.read_text().splitlines()
    assert any(c.startswith("iam update-assume-role-policy --role-name homefront-agent") for c in made)
    assert "iam tag-role --role-name homefront-agent --tags Key=project,Value=homefront" in made
    assert not any(c.startswith("iam create-role") for c in made)


def test_apply_creates_a_missing_role_with_path_duration_and_tag(tmp_path):
    result, calls = _dry_run(tmp_path, "--apply", exit_code=0, get_role_exit=254)  # get-role: no such role
    assert result.returncode == 0, result.stderr
    made = calls.read_text()
    create = made[made.index("iam create-role"):made.index("iam put-role-policy")]  # its policy JSON spans lines
    assert create.startswith("iam create-role --role-name homefront-agent --path / --max-session-duration 3600")
    assert create.rstrip().endswith("--tags Key=project,Value=homefront")
