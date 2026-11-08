"""A staffer stops their own running task: POST /api/v1/sessions/{sid}/revoke.

Same fail-closed path as offboarding, but for one conversation and only by its owner.
moto DynamoDB and a fake agent launcher: no real process, no AWS, no Bedrock.
"""

from __future__ import annotations

from app.main import app
from app.routers import sessions
from app.services import agent_supervisor
from app.services.agent_supervisor import AgentSupervisor

from .test_agent_autostart import FakeLauncher
from .test_conversations import DANA, login, open_conversation
from .test_gateway_api import (  # noqa: F401 (fixtures)
    CHIEF,
    REVOKED,
    _env,
    as_agent,
    bearer,
    client,
    gateway,
    get_mandate,
    store,
    table,
)

CALL = {"tool": "case_read", "arguments": {"case_id": "case-1"}}


def revoke(client, sid, who=None):
    login(who) if who else login()
    return client.post(f"/api/v1/sessions/{sid}/revoke", json={})


def test_the_owner_stops_their_task_and_the_next_tool_call_is_denied(client, store):
    sid = open_conversation(client, "check the inbox")
    token = get_mandate(client, sid)
    ok = client.post("/api/v1/mcp/call", json=CALL, headers=bearer(token))
    assert ok.status_code == 200 and "allowed" in ok.text

    r = revoke(client, sid)

    assert r.status_code == 200, r.text
    assert r.json() == {"sid": sid, "status": "revoked", "alreadyRevoked": False}
    assert store.get_session(sid).status == "revoked"
    # The very next call with the token the agent already holds is a DENY: one call, not one token lifetime.
    nxt = client.post("/api/v1/mcp/call", json=CALL, headers=bearer(token))
    assert "DENY" in nxt.text and REVOKED in nxt.text
    assert "Pothole" not in nxt.text


def test_after_a_stop_the_agent_cannot_mint_a_new_mandate(client):
    sid = open_conversation(client, "check the inbox")
    assert revoke(client, sid).status_code == 200
    as_agent()
    r = client.post(f"/api/v1/sessions/{sid}/mandate", json={})
    assert r.status_code == 403


def test_another_staffer_cannot_stop_someone_elses_task(client, store):
    sid = open_conversation(client, "mine")
    r = revoke(client, sid, DANA)
    assert r.status_code == 404  # as if it did not exist
    assert store.get_session(sid).status == "active"


def test_an_admin_cannot_stop_another_staffers_task_from_the_chat_route(client, store):
    sid = open_conversation(client, "mine")
    r = revoke(client, sid, CHIEF)  # admins cut access with Offboard; this route is owner-only
    assert r.status_code == 404
    assert store.get_session(sid).status == "active"


def test_the_agents_mandate_token_cannot_call_the_route(client, store):
    sid = open_conversation(client, "mine")
    token = get_mandate(client, sid)
    app.dependency_overrides.pop(sessions.require_staff, None)  # the real staff-token check, no login override
    r = client.post(f"/api/v1/sessions/{sid}/revoke", json={}, headers=bearer(token))
    assert r.status_code == 401
    assert store.get_session(sid).status == "active"


def test_no_login_is_401(client):
    app.dependency_overrides.pop(sessions.require_staff, None)
    assert client.post("/api/v1/sessions/some-sid/revoke", json={}).status_code == 401


def test_unknown_session_is_404(client):
    assert revoke(client, "no-such-sid").status_code == 404


def test_stopping_twice_is_fine_and_audited_once(client, store):
    sid = open_conversation(client, "check the inbox")
    first, second = revoke(client, sid), revoke(client, sid)
    assert first.status_code == second.status_code == 200
    assert second.json() == {"sid": sid, "status": "revoked", "alreadyRevoked": True}
    events, _ = store.events_since(None, limit=500)
    stops = [e for e in events if e.sid == sid and e.tool == sessions.REVOKE_TOOL]
    assert len(stops) == 1


def test_the_stop_is_an_audit_event_with_atlas_labels(client, store):
    sid = open_conversation(client, "check the inbox")
    assert revoke(client, sid).status_code == 200
    events, _ = store.events_since(None, limit=500)
    (event,) = [e for e in events if e.sid == sid and e.tool == sessions.REVOKE_TOOL]
    assert event.acting_for == "maya" and event.office == "rep-x"
    assert event.decision == "DENY" and event.reason == REVOKED
    assert "AML.T0053" in event.atlas
    assert event.resource == "stopped by maya"


def test_the_stop_ends_that_conversations_agent_only(client):
    fake = FakeLauncher()
    agent_supervisor.set_supervisor(AgentSupervisor(fake, max_starts=3, max_running_per_staffer=3))
    try:
        stopped = open_conversation(client, "first task")
        kept = open_conversation(client, "second task")
        assert {sid for sid, _ in fake.started} >= {stopped, kept}
        assert revoke(client, stopped).status_code == 200
    finally:
        agent_supervisor.set_supervisor(None)
    procs = dict(fake.started)
    assert procs[stopped].terminated, "the stopped conversation's agent is ended"
    assert not procs[kept].terminated, "the staffer's other conversations keep running"
