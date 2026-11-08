"""Activity log: durable storage, trace ids, actors and the /status report."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import ADMIN, ADMIN_USER, auth
from tests.test_runner import response, text, tool_use, use_model  # noqa: F401 (fixture)

from app.core.dynamodb import table  # noqa: E402
from identity_gateway import agents_api as gateway  # noqa: E402
from identity_gateway.audit import (  # noqa: E402
    DynamoAuditRepository,
    MemoryAuditRepository,
    compact_atlas,
)
from identity_gateway.store import GatewayStore  # noqa: E402

# Every call signs in as an admin with our Cognito login (tests/helpers.py).
client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login", "old_tables")
ALICE = "00000000-0000-0000-0000-0000000a11ce"


def _id() -> str:
    return f"agent-log-{uuid.uuid4().hex[:8]}"


def _create(headers=None, **body):
    r = client.post(
        "/api/v1/agents",
        json={"agent_id": _id(), "name": "Logged", "agency": "Dept.", "kind": "research", **body},
        headers=headers or {},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _events(headers=None, **params):
    r = client.get("/api/v1/audit", params=params, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def dynamo_audit():
    """Route the gateway's activity log to the (moto) DynamoDB audit table."""
    saved = gateway.store._audit
    gateway.store.use_audit_repository(DynamoAuditRepository(table("audit")))
    yield
    gateway.store.use_audit_repository(saved)


# --- what gets logged ---------------------------------------------------------


def test_agent_creation_is_logged_with_the_user():
    alice = auth(ALICE)
    agent = _create(alice)
    [event] = _events(alice, agent_id=agent["agent_id"], type="agent_created")
    assert event["actor"] == f"user:{ALICE}"
    assert event["kind"] == "research"
    from identity_gateway.agent_types import AGENT_TYPES

    assert event["tool_grants"] == sorted(AGENT_TYPES["research"].default_tools)


def test_agent_update_and_grant_changes_name_the_actor():
    agent = _create()
    aid = agent["agent_id"]
    client.patch(f"/api/v1/agents/{aid}", json={"max_steps": 3})
    client.post(f"/api/v1/agents/{aid}/grants/web.scrape/revoke")
    events = {e["type"]: e for e in _events(agent_id=aid)}
    assert events["agent_updated"]["fields"] == ["max_steps"]
    assert events["grant_revoked"]["actor"] == f"user:{ADMIN_USER}"


def test_manual_token_session_is_one_trace():
    agent = _create()
    aid = agent["agent_id"]
    auth_res = client.post("/api/v1/auth/token", json={"agent_id": aid}).json()
    session = auth_res["session_id"]
    headers = {"X-Agent-Token": auth_res['access_token']}
    client.post("/api/v1/agents/tool-call", json={"tool": "datagov.search", "arguments": {"q": "x"}}, headers=headers)
    client.post("/api/v1/agents/tool-call", json={"tool": "email.send", "arguments": {}}, headers=headers)

    trace = _events(trace_id=session)
    assert [e["type"] for e in reversed(trace)] == ["authenticate", "tool_call", "tool_call"]
    assert [e["decision"] for e in reversed(trace)] == ["allow", "allow", "deny"]
    assert all(e["actor"] == f"agent:{aid}" for e in trace if e["type"] == "tool_call")


def test_run_is_one_trace_from_start_to_finish(use_model):  # noqa: F811
    use_model(
        response(text("Searching."), tool_use("datagov_search", {"q": "housing"}), stop="tool_use"),
        response(text("Done.")),
    )
    agent = _create()
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/runs", json={"task": "Find housing data"})
    run_id = r.json()["run_id"]

    trace = list(reversed(_events(trace_id=run_id)))
    assert [e["type"] for e in trace] == ["run_started", "authenticate", "tool_call", "run_finished"]
    assert trace[0]["detail"] == "Find housing data"
    assert trace[-1]["status"] == "completed"


def test_filters_by_decision():
    agent = _create()
    tok = client.post("/api/v1/auth/token", json={"agent_id": agent["agent_id"]}).json()["access_token"]
    client.post(
        "/api/v1/agents/tool-call",
        json={"tool": "social.post", "arguments": {}},
        headers={"X-Agent-Token": tok},
    )
    denies = _events(agent_id=agent["agent_id"], decision="deny")
    assert denies and all(e["decision"] == "deny" for e in denies)


def test_other_users_agent_filter_returns_nothing():
    aid = _create(auth(ALICE))["agent_id"]
    assert _events(auth(), agent_id=aid) == []


def test_atlas_is_stored_compact():
    agent = _create()
    client.post("/api/v1/auth/token", json={"agent_id": agent["agent_id"]})
    [event] = _events(agent_id=agent["agent_id"], type="authenticate")
    assert set(event["atlas"]) <= {"event", "technique_ids", "mitigation_ids", "narrative", "residual_risk"}
    assert compact_atlas({"techniques": [{"description": "long"}], "narrative": "n"}) == {"narrative": "n"}


# --- DynamoDB backend ------------------------------------------------------------


def test_events_survive_in_dynamodb(dynamo_audit):
    agent = _create()
    aid = agent["agent_id"]
    client.post("/api/v1/auth/token", json={"agent_id": aid})

    # A fresh store (as after a restart) reading the same table sees the events.
    restarted = GatewayStore(audit=DynamoAuditRepository(table("audit")))
    events = restarted.list_audit([aid])
    assert [e["type"] for e in events] == ["authenticate", "agent_created"]
    assert isinstance(events[0]["ts"], float)
    assert "sk" not in events[0] and "expires_at" not in events[0]


def test_dynamo_items_carry_ttl():
    repo = DynamoAuditRepository(table("audit"), ttl_seconds=60)
    aid = _id()
    repo.append({"id": "aud_ttl", "ts": 1000.5, "agent_id": aid, "type": "x", "decision": "info"})
    item = table("audit").scan()["Items"]
    [mine] = [i for i in item if i["agent_id"] == aid]
    assert int(mine["expires_at"]) == 1060


def test_dynamo_filters_page_past_non_matching_items():
    repo = DynamoAuditRepository(table("audit"))
    aid = _id()
    for i in range(30):
        repo.append({"id": f"aud_{i:03}", "ts": 1000.0 + i, "agent_id": aid, "type": "tool_call", "decision": "allow"})
    repo.append({"id": "aud_old_deny", "ts": 999.0, "agent_id": aid, "type": "tool_call", "decision": "deny"})
    [deny] = repo.list([aid], 5, decision="deny")
    assert deny["id"] == "aud_old_deny"
    newest = repo.list([aid], 3)
    assert [e["id"] for e in newest] == ["aud_029", "aud_028", "aud_027"]


# --- failures and status ---------------------------------------------------------------


class _Broken:
    backend = "dynamodb"

    def append(self, event):
        from botocore.exceptions import ClientError

        raise ClientError({"Error": {"Code": "ExpiredTokenException", "Message": "arn:aws:secret"}}, "PutItem")

    def list(self, agent_ids, limit, **filters):
        return []


def test_failed_writes_are_kept_and_reported():
    store = GatewayStore(audit=_Broken())
    store.append_audit({"type": "x", "decision": "info", "agent_id": "agent-a"})
    assert store.audit_write_errors == 1
    assert store.last_audit_error == "ExpiredTokenException"  # code only, no ARN
    assert [e["type"] for e in store.list_audit(["agent-a"])] == ["x"]


def test_status_reports_the_active_stores():
    r = client.get("/api/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_store"] == "memory"
    assert body["audit_store"] in {"memory", "dynamodb"}
    assert "warnings" in body and "audit_write_errors" in body


def test_startup_falls_back_per_table_with_a_warning(monkeypatch):
    from app import agent_iam

    store = GatewayStore()
    monkeypatch.setenv("DYNAMODB_TABLE_PREFIX", "missing-")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        agent_iam.use_dynamodb_stores(store)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert (store.agent_backend, store.audit_backend) == ("memory", "memory")
    assert any("missing-audit" in w and "ResourceNotFoundException" in w for w in store.storage_warnings)


def test_memory_repository_keeps_newest():
    repo = MemoryAuditRepository(limit=2)
    for i in range(3):
        repo.append({"id": str(i), "agent_id": "a"})
    assert [e["id"] for e in repo.list(["a"], 10)] == ["2", "1"]
