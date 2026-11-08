"""AAuth binding on the Agents / Monitoring workload-token pathway."""

from __future__ import annotations

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import ADMIN

client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login", "old_tables")


@pytest.fixture(autouse=True)
def _aauth_on(monkeypatch):
    monkeypatch.setenv("HOMEFRONT_AUTH_PROTOCOLS", "legacy,aauth,idjag")
    monkeypatch.setenv("MANDATE_SIGNING_SECRET", "test-mandate-signing-secret-32chars!!")
    monkeypatch.setenv("AAUTH_SIGNING_KEY", "test-mandate-signing-secret-32chars!!")


def test_auth_token_issues_aauth_bound_workload_identity():
    body = client.post("/api/v1/auth/token", json={"agent_id": "agent-comms-01"}).json()
    assert body["access_token"].startswith("gr_")
    assert body["auth_protocol"] == "aauth"
    assert body["jkt"]
    assert body["agent_jwt"]
    assert body["aa_auth_jwt"]

    agent_header = jwt.get_unverified_header(body["agent_jwt"])
    assert agent_header["typ"] == "aa-agent+jwt"
    auth_header = jwt.get_unverified_header(body["aa_auth_jwt"])
    assert auth_header["typ"] == "aa-auth+jwt"
    claims = jwt.decode(body["aa_auth_jwt"], options={"verify_signature": False})
    assert claims["auth_protocol"] == "aauth"
    assert claims["cnf"]["jkt"] == body["jkt"]
    assert claims["sub"] == "agent-comms-01"


def test_tool_call_audit_stamps_aauth_protocol():
    tok = client.post("/api/v1/auth/token", json={"agent_id": "agent-comms-01"}).json()
    r = client.post(
        "/api/v1/agents/tool-call",
        json={"tool": "datagov.search", "arguments": {"query": "housing"}},
        headers={"X-Agent-Token": tok["access_token"]},
    )
    # demo agent may or may not have datagov.search; either way audit should stamp aauth
    audit = client.get("/api/v1/audit?limit=20").json()
    auth_events = [e for e in audit if e.get("type") == "authenticate" and e.get("agent_id") == "agent-comms-01"]
    assert auth_events
    assert auth_events[0]["auth_protocol"] == "aauth"

    tool_events = [
        e
        for e in audit
        if e.get("type") == "tool_call" and e.get("agent_id") == "agent-comms-01"
    ]
    if r.status_code in (200, 403):
        assert tool_events
        assert tool_events[0]["auth_protocol"] == "aauth"


def test_legacy_only_skips_aauth(monkeypatch):
    monkeypatch.setenv("HOMEFRONT_AUTH_PROTOCOLS", "legacy")
    body = client.post("/api/v1/auth/token", json={"agent_id": "agent-comms-01"}).json()
    assert body["auth_protocol"] == "legacy_workload"
    assert body.get("agent_jwt") in (None, "")
    assert body.get("aa_auth_jwt") in (None, "")
