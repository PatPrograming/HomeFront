"""Broker, offboard, events and dashboard-call routes (moto DynamoDB, no AWS, no Cognito).

Fixtures live in this file because Backend/tests/conftest.py is shared and the gateway
package's conftest is not on this test directory's path.
"""

from __future__ import annotations

import secrets

import boto3
import pytest
from fastapi.testclient import TestClient
from identity_gateway.authz import StarterPolicyAuthorizer
from identity_gateway.gateway import Gateway, set_gateway
from identity_gateway.storage import MandateStore, create_table
from identity_gateway.tokens import InvalidToken, verify_mandate_token
from mandate_contracts import TOOLS
from mandate_contracts.ddb import CaseItem, StafferItem
from moto import mock_aws

from app.main import app
from app.routers import events, sessions, staff
from app.routers.sessions import AgentClientIdentity, StaffIdentity

SECRET = secrets.token_hex(24)  # random per test run, 48 chars (>= 32)
REVOKED = "session revoked / delegator inactive"

MAYA = StaffIdentity("maya", "sub-maya", "maya@example.gov", frozenset({"staffer"}))
CHIEF = StaffIdentity("chiefofstaff", "sub-chief", "chief@example.gov", frozenset({"admin"}))
AGENT = AgentClientIdentity("agent-client-1", frozenset({"mandate/tools"}))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "MANDATE_SIGNING_SECRET": SECRET,
        "HOMEFRONT_LIVE_DATAGOV": "0",
        "HOMEFRONT_LIVE_SCRAPE": "0",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def table():
    with mock_aws():
        yield create_table(boto3.resource("dynamodb", region_name="us-east-1"))


@pytest.fixture
def store(table):
    s = MandateStore(table)
    s.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer"))
    s.put_staffer("chiefofstaff", StafferItem(active=True, office="rep-x", role="admin"))
    s.put_staffer("dana", StafferItem(active=True, office="rep-z", role="staffer"))
    s.put_staffer("retired", StafferItem(active=False, office="rep-x", role="staffer"))
    s.put_case(
        "case-1",
        CaseItem(
            office="rep-x", constituent="Jane Doe", constituent_email="jane.doe@example.com",
            topic="Pothole on Main St", status="open",
        ),
    )
    return s


@pytest.fixture
def gateway(store):
    g = Gateway(store, StarterPolicyAuthorizer(), SECRET)
    set_gateway(g)
    return g


@pytest.fixture
def client(gateway):
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def as_staff(identity=MAYA):
    app.dependency_overrides[sessions.require_staff] = lambda: identity
    app.dependency_overrides[events.require_staff] = lambda: identity


def as_admin(identity=CHIEF):
    app.dependency_overrides[staff.require_admin] = lambda: identity


def as_agent(identity=AGENT):
    app.dependency_overrides[sessions.require_agent_client] = lambda: identity


def new_session(client, who=MAYA) -> dict:
    as_staff(who)
    r = client.post("/api/v1/sessions", json={})
    assert r.status_code == 200, r.text
    return r.json()


def get_mandate(client, sid) -> str:
    as_agent()
    r = client.post(f"/api/v1/sessions/{sid}/mandate", json={})
    assert r.status_code == 200, r.text
    return r.json()["mandateToken"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---- every protected route denies by default ------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/sessions"),
        ("post", "/api/v1/sessions/some-sid/mandate"),
        ("post", "/api/v1/staff/maya/offboard"),
        ("get", "/api/v1/events"),
    ],
)
def test_protected_routes_are_401_without_a_verified_identity(client, method, path):
    r = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
    assert r.status_code == 401


def test_catalog_lists_the_seven_mandate_tools(client):
    r = client.get("/api/v1/tools")
    assert r.status_code == 200
    for name in TOOLS:
        assert name in r.text


def test_in_memory_agent_admin_routes_are_removed(client):
    assert client.get("/api/v1/agents").status_code == 404
    assert client.get("/api/v1/audit").status_code == 404
    assert client.post("/api/v1/agents/agent-comms-01/grants/email.send/revoke").status_code == 404


def test_open_token_endpoint_is_gone(client):
    for method in ("post", "get"):
        r = getattr(client, method)("/api/v1/auth/token", **({"json": {"agent_id": "a"}} if method == "post" else {}))
        assert r.status_code in (404, 405)


# ---- POST /sessions -------------------------------------------------------


def test_staffer_creates_a_session_with_facts_from_the_database(client, store):
    body = new_session(client)
    assert set(body) >= {"sid", "agent", "actingFor", "office", "mode", "allowedTools", "expiresAt"}
    assert body["actingFor"] == "maya"
    assert body["office"] == "rep-x"
    assert body["mode"] == "official"
    assert body["sid"] and body["agent"] and body["expiresAt"]
    assert "records_bulk_export" not in body["allowedTools"]
    assert body["allowedTools"] and set(body["allowedTools"]) <= set(TOOLS)

    item = store.get_session(body["sid"])
    assert item.status == "active"
    assert (item.acting_for, item.office, item.mode, item.agent) == (
        "maya", "rep-x", "official", body["agent"],
    )


def test_session_office_comes_from_the_staffer_item_not_the_token(client):
    dana = StaffIdentity("dana", "sub-dana", None, frozenset({"staffer"}))
    assert new_session(client, dana)["office"] == "rep-z"


def test_each_session_gets_a_fresh_sid(client):
    assert new_session(client)["sid"] != new_session(client)["sid"]


@pytest.mark.parametrize("username", ["retired", "ghost"])
def test_inactive_or_unknown_staffer_cannot_open_a_session(client, store, username):
    as_staff(StaffIdentity(username, "sub", None, frozenset({"staffer"})))
    r = client.post("/api/v1/sessions", json={})
    assert r.status_code == 403


# ---- POST /sessions/{sid}/mandate ----------------------------------------


def test_agent_gets_a_mandate_token_for_an_active_session(client):
    sess = new_session(client)
    as_agent()
    r = client.post(f"/api/v1/sessions/{sess['sid']}/mandate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["mandateToken"] and body["expiresAt"]
    claims = verify_mandate_token(body["mandateToken"], SECRET)
    assert claims.act == "maya"
    assert claims.sid == sess["sid"]
    assert claims.sub == sess["agent"]
    assert claims.office == "rep-x"
    assert claims.exp - claims.iat == 15 * 60


def test_mandate_needs_the_agent_credential_even_with_a_staff_login(client):
    sess = new_session(client)  # staff override only
    app.dependency_overrides.pop(sessions.require_agent_client, None)
    r = client.post(f"/api/v1/sessions/{sess['sid']}/mandate", json={})
    assert r.status_code == 401


def test_no_mandate_for_a_revoked_session(client, store):
    sess = new_session(client)
    store.revoke_sessions_for("maya")
    as_agent()
    assert client.post(f"/api/v1/sessions/{sess['sid']}/mandate", json={}).status_code == 403


def test_no_mandate_when_the_staffer_is_inactive(client, store):
    sess = new_session(client)
    store.set_staffer_active("maya", False)
    as_agent()
    assert client.post(f"/api/v1/sessions/{sess['sid']}/mandate", json={}).status_code == 403


def test_no_mandate_for_an_unknown_session(client):
    as_agent()
    r = client.post("/api/v1/sessions/does-not-exist/mandate", json={})
    assert r.status_code in (403, 404)


# ---- POST /staff/{id}/offboard -------------------------------------------


def test_admin_offboards_a_staffer_and_revokes_all_their_sessions(client, store):
    s1, s2 = new_session(client), new_session(client)
    other = new_session(client, StaffIdentity("dana", "sub-dana", None, frozenset({"staffer"})))
    as_admin()
    r = client.post("/api/v1/staff/maya/offboard", json={})
    assert r.status_code == 200
    assert r.json() == {"staffer": "maya", "active": False, "revokedSessions": 2}
    assert store.get_staffer("maya").active is False
    assert store.get_session(s1["sid"]).status == "revoked"
    assert store.get_session(s2["sid"]).status == "revoked"
    assert store.get_session(other["sid"]).status == "active"


def test_offboard_requires_the_admin_dependency(client):
    as_staff()  # a staffer login must not be enough
    r = client.post("/api/v1/staff/maya/offboard", json={})
    assert r.status_code == 401


def test_old_mandate_token_is_denied_after_offboarding(client, store):
    sess = new_session(client)
    token = get_mandate(client, sess["sid"])
    call = {"tool": "case_read", "arguments": {"case_id": "case-1"}}

    ok = client.post("/api/v1/mcp/call", json=call, headers=bearer(token))
    assert ok.status_code == 200 and "allowed" in ok.text

    as_admin()
    assert client.post("/api/v1/staff/maya/offboard", json={}).status_code == 200

    r = client.post("/api/v1/mcp/call", json=call, headers=bearer(token))
    assert r.status_code in (200, 403)  # a DENY decision, not an auth failure
    assert "DENY" in r.text and REVOKED in r.text
    assert "Pothole" not in r.text


# ---- POST /mcp/call (dashboard path) -------------------------------------


def test_dashboard_call_without_a_token_is_401(client):
    r = client.post("/api/v1/mcp/call", json={"tool": "case_read", "arguments": {"case_id": "case-1"}})
    assert r.status_code == 401


def test_dashboard_call_with_a_bad_token_is_401_and_audited(client, store):
    r = client.post(
        "/api/v1/mcp/call",
        json={"tool": "case_read", "arguments": {"case_id": "case-1"}},
        headers=bearer("garbage"),
    )
    assert r.status_code == 401
    events_, _ = store.events_since(None)
    assert [(e.decision, e.reason) for e in events_] == [("DENY", "invalid token")]


def test_dashboard_call_goes_through_cedar(client):
    token = get_mandate(client, new_session(client)["sid"])
    r = client.post(
        "/api/v1/mcp/call",
        json={"tool": "records_bulk_export", "arguments": {"query": "all"}},
        headers=bearer(token),
    )
    assert r.status_code in (200, 403)
    assert "DENY" in r.text and "forbid-bulk-export" in r.text


# ---- GET /events ----------------------------------------------------------


def test_events_feed_is_camel_case_oldest_first_with_a_cursor(client, gateway):
    token = get_mandate(client, new_session(client)["sid"])
    gateway.guarded("case_read", {"case_id": "case-1"}, token)
    gateway.guarded("records_bulk_export", {"query": "all"}, token)

    as_staff()
    body = client.get("/api/v1/events").json()
    assert set(body) == {"events", "next"}
    first, second = body["events"]
    assert (first["tool"], first["decision"]) == ("case_read", "ALLOW")
    assert (second["tool"], second["decision"]) == ("records_bulk_export", "DENY")
    assert set(first) >= {"id", "ts", "sid", "agent", "actingFor", "office", "policyIds", "atlas", "guardrail"}
    assert first["actingFor"] == "maya"
    assert body["next"] == f"{second['ts']}#{second['id']}"


def test_events_since_returns_only_newer_events_and_echoes_the_cursor_when_idle(client, gateway):
    token = get_mandate(client, new_session(client)["sid"])
    gateway.guarded("case_read", {"case_id": "case-1"}, token)
    as_staff()
    cursor = client.get("/api/v1/events").json()["next"]

    gateway.guarded("inbox_read", {"email_id": "missing"}, token)
    newer = client.get("/api/v1/events", params={"since": cursor}).json()
    assert [e["tool"] for e in newer["events"]] == ["inbox_read"]

    idle = client.get("/api/v1/events", params={"since": newer["next"]}).json()
    assert idle == {"events": [], "next": newer["next"]}


# ---- restart --------------------------------------------------------------


def test_restart_keeps_offboarding_revocations_and_events(client, table):
    sess = new_session(client)
    token = get_mandate(client, sess["sid"])
    call = {"tool": "case_read", "arguments": {"case_id": "case-1"}}
    assert client.post("/api/v1/mcp/call", json=call, headers=bearer(token)).status_code == 200
    as_admin()
    client.post("/api/v1/staff/maya/offboard", json={})
    as_staff(CHIEF)
    before = client.get("/api/v1/events").json()["events"]
    assert before

    # Restart: new store and gateway objects over the same table.
    store2 = MandateStore(table)
    set_gateway(Gateway(store2, StarterPolicyAuthorizer(), SECRET))

    assert store2.get_staffer("maya").active is False
    assert store2.get_session(sess["sid"]).status == "revoked"
    assert client.get("/api/v1/events").json()["events"] == before
    r = client.post("/api/v1/mcp/call", json=call, headers=bearer(token))
    assert REVOKED in r.text
    as_staff()
    assert client.post("/api/v1/sessions", json={}).status_code == 403


# ---- the verifier is the same one the routes use -------------------------


def test_token_minted_by_the_broker_is_rejected_under_a_different_secret(client):
    token = get_mandate(client, new_session(client)["sid"])
    with pytest.raises(InvalidToken):
        verify_mandate_token(token, "some-other-secret-some-other-secret-1")


# ---- round 2: session lifetime, future cursor, auth fallback ---------------


def _iso_ago(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _set_created_at(table, sid, value):
    table.update_item(
        Key={"pk": f"SESSION#{sid}", "sk": "META"},
        UpdateExpression="SET createdAt = :c",
        ExpressionAttributeValues={":c": value},
    )


def test_session_ttl_constant_is_one_hour():
    assert sessions.SESSION_TTL_SECONDS == 3600


def test_session_expires_at_is_created_at_plus_one_hour(client, store):
    from datetime import datetime

    sess = new_session(client)
    created = datetime.fromisoformat(store.get_session(sess["sid"]).created_at.replace("Z", "+00:00"))
    expires = datetime.fromisoformat(sess["expiresAt"].replace("Z", "+00:00"))
    assert (expires - created).total_seconds() == 3600


def test_mandate_token_still_lasts_15_minutes(client):
    sess = new_session(client)
    claims = verify_mandate_token(get_mandate(client, sess["sid"]), SECRET)
    assert claims.exp - claims.iat == 900


def test_no_mandate_for_a_session_older_than_one_hour(client, table):
    sess = new_session(client)
    _set_created_at(table, sess["sid"], _iso_ago(3601))
    as_agent()
    assert client.post(f"/api/v1/sessions/{sess['sid']}/mandate", json={}).status_code == 403


def test_mandate_still_issued_for_a_session_younger_than_one_hour(client, table):
    sess = new_session(client)
    _set_created_at(table, sess["sid"], _iso_ago(3000))
    as_agent()
    assert client.post(f"/api/v1/sessions/{sess['sid']}/mandate", json={}).status_code == 200


def test_no_mandate_when_session_created_at_is_not_an_iso_timestamp(client, table):
    sess = new_session(client)
    _set_created_at(table, sess["sid"], "yesterday-ish")
    as_agent()
    assert client.post(f"/api/v1/sessions/{sess['sid']}/mandate", json={}).status_code == 403


def test_events_since_in_the_future_is_400(client):
    as_staff()
    r = client.get("/api/v1/events", params={"since": "2099-01-01T00:00:00.000Z#abc"})
    assert r.status_code == 400


def test_events_since_malformed_is_400(client):
    as_staff()
    assert client.get("/api/v1/events", params={"since": "not-a-cursor"}).status_code == 400


def test_routers_use_the_auth_stub_when_app_core_cognito_is_absent():
    import importlib.util

    import identity_gateway.auth_stub as stub

    if importlib.util.find_spec("app.core.cognito") is not None:
        pytest.skip("app.core.cognito exists in this branch")
    assert sessions.require_staff is stub.require_staff
    assert sessions.require_agent_client is stub.require_agent_client
    assert events.require_staff is stub.require_staff
