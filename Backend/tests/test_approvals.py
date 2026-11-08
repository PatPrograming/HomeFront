"""Human approval for requires_hitl tools (email.send, social.post, social.reply)."""
import json
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import auth
from tests.test_runner import response, text, tool_use, use_model  # noqa: F401 (fixture)

from app.core.dynamodb import table  # noqa: E402
from identity_gateway import app as gateway  # noqa: E402
from identity_gateway.approvals import (  # noqa: E402
    ApprovalConflict,
    ApprovalRecord,
    DynamoApprovalRepository,
    MemoryApprovalRepository,
)

client = TestClient(app)
ALICE = "00000000-0000-0000-0000-0000000a11ce"
EMAIL = {"to": "resident@example.gov", "subject": "Town hall", "body": "Tuesday at 6pm."}


def _agent(headers=None, kind="email", **body):
    body.setdefault("recipients", [EMAIL["to"]])  # email.send only reaches the list
    r = client.post(
        "/api/v1/agents",
        json={"agent_id": f"agent-apr-{uuid.uuid4().hex[:8]}", "name": "Comms", "agency": "City", "kind": kind, **body},
        headers=headers or {},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _token(agent_id, headers=None):
    r = client.post("/api/v1/auth/token", json={"agent_id": agent_id}, headers=headers or {})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["session_id"]


def _call(tool, args, agent_headers):
    return client.post("/api/v1/mcp/call", json={"tool": tool, "arguments": args}, headers=agent_headers)


def _request(headers=None, tool="email.send", args=EMAIL, **agent_body):
    agent = _agent(headers, **agent_body)
    agent_headers, session = _token(agent["agent_id"], headers)
    r = _call(tool, args, agent_headers)
    assert r.status_code == 200, r.text
    return agent, r.json(), session


@pytest.fixture(params=["memory", "dynamodb"])
def approvals_backend(request):
    """Run a test against both approval stores."""
    saved = gateway.store.approvals
    if request.param == "dynamodb":
        gateway.store.use_approval_repository(DynamoApprovalRepository(table("approvals")))
    else:
        gateway.store.use_approval_repository(MemoryApprovalRepository())
    yield request.param
    gateway.store.use_approval_repository(saved)


# --- requesting --------------------------------------------------------------------


def test_hitl_tool_is_queued_not_executed(approvals_backend):
    agent, out, session = _request()
    assert out["decision"] == "pending_approval"
    assert out["result"]["status"] == "pending_approval"
    assert "message_id" not in out["result"]  # the email handler never ran

    pending = client.get("/api/v1/approvals", params={"status": "pending"}).json()
    [mine] = [a for a in pending if a["approval_id"] == out["approval_id"]]
    assert mine["arguments"] == EMAIL
    assert mine["trace_id"] == session
    assert mine["agent"]["name"] == "Comms"
    assert set(mine["input_schema"]["properties"]) == {"to", "subject", "body"}


def test_non_hitl_tools_still_run_immediately():
    agent = _agent(kind="research")
    headers, _ = _token(agent["agent_id"])
    r = _call("datagov.search", {"q": "housing"}, headers)
    assert r.json()["decision"] == "allow"


def test_ungranted_hitl_tool_is_denied_not_queued():
    agent = _agent(tool_grants=["datagov.search"])
    headers, _ = _token(agent["agent_id"])
    r = _call("email.send", EMAIL, headers)
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "tool_not_granted"


# --- deciding ------------------------------------------------------------------------


def test_approve_executes_once_and_is_traced(approvals_backend):
    agent, out, session = _request()
    aid = out["approval_id"]
    r = client.post(f"/api/v1/approvals/{aid}/approve", json={"note": "Looks right"})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "executed"
    assert done["result"]["status"] == "simulated" and done["result"]["to"] == EMAIL["to"]
    assert done["decided_by"] == "anonymous" and done["note"] == "Looks right"
    assert done["edited"] is False

    again = client.post(f"/api/v1/approvals/{aid}/approve", json={})
    assert again.status_code == 409 and again.json()["detail"]["status"] == "executed"

    trace = [e["type"] for e in reversed(client.get("/api/v1/audit", params={"trace_id": session}).json())]
    assert trace == ["authenticate", "approval_requested", "approval_approved", "tool_call"]


def test_approve_with_edits_keeps_the_original(approvals_backend):
    _, out, _ = _request()
    edited = {**EMAIL, "subject": "Town hall: Tuesday 6pm"}
    done = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={"arguments": edited}).json()
    assert done["arguments"] == edited
    assert done["original_arguments"] == EMAIL
    assert done["edited"] is True
    assert done["result"]["subject"] == "Town hall: Tuesday 6pm"


@pytest.mark.parametrize(
    "bad",
    [
        {**EMAIL, "bcc": "attacker@evil.example"},  # extra field
        {"to": "a@b.gov", "subject": "s"},  # missing body
        {**EMAIL, "body": 42},  # not text
    ],
)
def test_edited_arguments_must_match_schema(bad):
    _, out, _ = _request()
    r = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={"arguments": bad})
    assert r.status_code == 400
    assert client.get(f"/api/v1/approvals/{out['approval_id']}").json()["status"] == "pending"


def test_reject_never_executes(approvals_backend):
    agent, out, _ = _request()
    aid = out["approval_id"]
    done = client.post(f"/api/v1/approvals/{aid}/reject", json={"note": "Wrong recipient"}).json()
    assert done["status"] == "rejected" and done["result"] is None
    assert client.post(f"/api/v1/approvals/{aid}/approve", json={}).status_code == 409
    events = client.get("/api/v1/audit", params={"agent_id": agent["agent_id"], "type": "approval_rejected"}).json()
    assert events[0]["detail"] == "Rejected: Wrong recipient"


def test_revoke_after_request_blocks_approval():
    agent, out, _ = _request()
    client.post(f"/api/v1/agents/{agent['agent_id']}/grants/email.send/revoke")
    r = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "no_longer_authorized"
    # Still pending: re-granting lets someone approve it later.
    assert client.get(f"/api/v1/approvals/{out['approval_id']}").json()["status"] == "pending"
    client.post(f"/api/v1/agents/{agent['agent_id']}/grants/email.send")
    assert client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={}).status_code == 200


def test_expired_request_cannot_be_approved():
    _, out, _ = _request()
    aid = out["approval_id"]
    gateway.store.approvals.transition(aid, "pending", expires_at=0.0)
    assert client.get(f"/api/v1/approvals/{aid}").json()["status"] == "expired"
    r = client.post(f"/api/v1/approvals/{aid}/approve", json={})
    assert r.status_code == 409 and r.json()["detail"]["status"] == "expired"


def test_failed_execution_is_recorded(monkeypatch):
    _, out, _ = _request()

    def boom(*a, **k):
        raise RuntimeError("SES down")

    monkeypatch.setattr(gateway, "invoke_tool", boom)
    r = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={})
    assert r.status_code == 502
    rec = client.get(f"/api/v1/approvals/{out['approval_id']}").json()
    assert rec["status"] == "failed" and "SES down" in rec["error"]


# --- who can decide ---------------------------------------------------------------


def test_other_users_cannot_see_or_decide():
    alice = auth(ALICE)
    _, out, _ = _request(alice)
    aid = out["approval_id"]
    bob = auth()
    assert client.get(f"/api/v1/approvals/{aid}", headers=bob).status_code == 404
    assert client.post(f"/api/v1/approvals/{aid}/approve", json={}, headers=bob).status_code == 404
    assert aid not in {a["approval_id"] for a in client.get("/api/v1/approvals", headers=bob).json()}
    done = client.post(f"/api/v1/approvals/{aid}/approve", json={}, headers=alice).json()
    assert done["decided_by"] == f"user:{ALICE}"


def test_approval_routes_require_login_when_configured(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("AGENT_ADMIN_REQUIRES_LOGIN", "true")
    get_settings.cache_clear()
    try:
        assert client.get("/api/v1/approvals").status_code == 401
        assert client.post("/api/v1/approvals/apr_x/approve", json={}).status_code == 401
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_agent_token_cannot_approve_its_own_request():
    agent = _agent()
    headers, _ = _token(agent["agent_id"])
    out = _call("email.send", EMAIL, headers).json()
    # An agent's workload token is not a user token: rejected outright.
    r = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={}, headers=headers)
    assert r.status_code == 401


# --- runs ----------------------------------------------------------------------------


def test_run_queues_email_and_model_is_told(use_model):  # noqa: F811
    fake = use_model(
        response(tool_use("email_send", EMAIL), stop="tool_use"),
        response(text("Drafted the email; it's awaiting approval.")),
    )
    agent = _agent()
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/runs", json={"task": "Email the resident"})
    run = client.get(f"/api/v1/agents/{agent['agent_id']}/runs/{r.json()['run_id']}").json()

    assert run["status"] == "completed"
    step = run["steps"][0]
    assert step["decision"] == "pending_approval" and step["approval_id"].startswith("apr_")
    told = json.loads(fake.calls[1]["messages"][-1]["content"][0]["content"])
    assert told["status"] == "pending_approval"
    assert "approval" in fake.calls[0]["system"]

    rec = client.get(f"/api/v1/approvals/{step['approval_id']}").json()
    assert rec["run_id"] == run["run_id"] and rec["trace_id"] == run["run_id"]


# --- storage -------------------------------------------------------------------------


@pytest.mark.parametrize("repo_factory", [MemoryApprovalRepository, lambda: DynamoApprovalRepository(table("approvals"))])
def test_transition_happens_once_under_concurrency(repo_factory):
    repo = repo_factory()
    rec = repo.insert(ApprovalRecord(agent_id="agent-x", tool="email.send", arguments=dict(EMAIL)))
    wins, losses = [], []

    def claim():
        try:
            repo.transition(rec.approval_id, "pending", status="approved")
            wins.append(1)
        except ApprovalConflict:
            losses.append(1)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(losses) == 7


def test_dynamo_round_trip_and_field_removal():
    repo = DynamoApprovalRepository(table("approvals"))
    rec = repo.insert(ApprovalRecord(agent_id="agent-x", tool="email.send", arguments=dict(EMAIL), run_id="run_1"))
    got = repo.get(rec.approval_id)
    assert got.arguments == EMAIL and got.run_id == "run_1" and isinstance(got.requested_at, float)
    after = repo.transition(rec.approval_id, "pending", status="approved", note="n", run_id=None)
    assert after.status == "approved" and after.note == "n" and after.run_id is None
    with pytest.raises(KeyError):
        repo.transition("apr_missing", "pending", status="approved")


def test_status_reports_approval_store():
    assert client.get("/api/v1/status").json()["approval_store"] in {"memory", "dynamodb"}
