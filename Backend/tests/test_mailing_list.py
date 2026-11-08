"""Per-agent mailing lists: an allowlist for email.send and the audience of email.send_list."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.test_runner import response, text, use_model  # noqa: F401 (fixture)

from identity_gateway import app as gateway  # noqa: E402
from identity_gateway.runner import system_prompt  # noqa: E402
from mcp_tools import handlers  # noqa: E402

client = TestClient(app)
LIST = ["ana@example.gov", "ben@example.gov"]


def _agent(recipients=None, kind="email"):
    body = {"agent_id": f"agent-ml-{uuid.uuid4().hex[:8]}", "name": "Comms", "agency": "City", "kind": kind}
    if recipients is not None:
        body["recipients"] = recipients
    r = client.post("/api/v1/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _call(agent_id, tool, args):
    r = client.post("/api/v1/auth/token", json={"agent_id": agent_id})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return client.post("/api/v1/mcp/call", json={"tool": tool, "arguments": args}, headers=headers)


def _put(agent_id, emails):
    return client.put(f"/api/v1/agents/{agent_id}/recipients", json={"emails": emails})


# --- managing the list -----------------------------------------------------------


def test_list_is_normalized_and_audited():
    agent = _agent()
    assert agent["recipients"] == []
    r = _put(agent["agent_id"], ["  Ben@Example.gov", "ana@example.gov", "ben@example.gov", ""])
    assert r.status_code == 200
    assert r.json()["recipients"] == LIST
    assert client.get(f"/api/v1/agents/{agent['agent_id']}").json()["recipients"] == LIST

    events = client.get("/api/v1/audit", params={"agent_id": agent["agent_id"], "type": "recipients_updated"}).json()
    assert events and events[0]["added"] == LIST


def test_invalid_addresses_are_rejected_and_nothing_changes():
    agent = _agent(LIST)
    r = _put(agent["agent_id"], ["ok@example.gov", "not-an-email", "a@b"])
    assert r.status_code == 400
    assert r.json()["detail"]["invalid"] == ["a@b", "not-an-email"]
    assert client.get(f"/api/v1/agents/{agent['agent_id']}").json()["recipients"] == LIST


def test_list_size_is_capped():
    agent = _agent()
    r = _put(agent["agent_id"], [f"u{i}@example.gov" for i in range(501)])
    assert r.status_code == 400


def test_create_rejects_invalid_recipients():
    r = client.post("/api/v1/agents", json={"name": "x", "agency": "y", "kind": "email", "recipients": ["nope"]})
    assert r.status_code == 400


# --- allowlist ---------------------------------------------------------------------


def test_send_to_unlisted_address_is_blocked_before_approval():
    agent = _agent(LIST)
    r = _call(agent["agent_id"], "email.send", {"to": "attacker@evil.example", "subject": "s", "body": "b"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "recipient_not_allowed"
    pending = [a for a in client.get("/api/v1/approvals").json() if a["agent_id"] == agent["agent_id"]]
    assert pending == []  # never reached a reviewer


def test_send_to_listed_address_is_case_insensitive():
    agent = _agent(LIST)
    r = _call(agent["agent_id"], "email.send", {"to": "ANA@example.gov", "subject": "s", "body": "b"})
    assert r.status_code == 200 and r.json()["decision"] == "pending_approval"


def test_reviewer_cannot_redirect_to_unlisted_address():
    agent = _agent(LIST)
    out = _call(agent["agent_id"], "email.send", {"to": LIST[0], "subject": "s", "body": "b"}).json()
    r = client.post(
        f"/api/v1/approvals/{out['approval_id']}/approve",
        json={"arguments": {"to": "someone@else.example", "subject": "s", "body": "b"}},
    )
    assert r.status_code == 403
    assert client.get(f"/api/v1/approvals/{out['approval_id']}").json()["status"] == "pending"


def test_removal_from_list_after_request_blocks_approval():
    agent = _agent(LIST)
    out = _call(agent["agent_id"], "email.send", {"to": LIST[0], "subject": "s", "body": "b"}).json()
    _put(agent["agent_id"], LIST[1:])
    r = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={})
    assert r.status_code == 403


# --- list send ----------------------------------------------------------------------


def test_list_send_goes_to_current_list(monkeypatch):
    sent = []
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "agency@example.gov")
    monkeypatch.setattr(handlers, "_ses_client", lambda: None)
    monkeypatch.setattr(handlers, "_ses_send", lambda ses, frm, to, subj, body: sent.append(to) or "m")

    agent = _agent(LIST)
    out = _call(agent["agent_id"], "email.send_list", {"subject": "Notice", "body": "Hi all"}).json()
    assert out["decision"] == "pending_approval"
    _put(agent["agent_id"], LIST + ["cy@example.gov"])

    view = client.get(f"/api/v1/approvals/{out['approval_id']}").json()
    assert view["recipients"] == LIST + ["cy@example.gov"]

    done = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={}).json()
    assert done["status"] == "executed"
    assert sent == LIST + ["cy@example.gov"]
    assert done["result"]["sent"] == 3 and done["result"]["failed"] == []


def test_list_send_reports_partial_failures(monkeypatch):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "agency@example.gov")
    monkeypatch.setattr(handlers, "_ses_client", lambda: None)

    def send(ses, frm, to, subj, body):
        if to == LIST[1]:
            raise RuntimeError("MessageRejected")
        return "m"

    monkeypatch.setattr(handlers, "_ses_send", send)
    agent = _agent(LIST)
    out = _call(agent["agent_id"], "email.send_list", {"subject": "s", "body": "b"}).json()
    done = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={}).json()
    assert done["result"]["status"] == "partially_sent"
    assert done["result"]["sent"] == 1 and done["result"]["failed"][0]["to"] == LIST[1]


def test_list_send_with_empty_list_is_blocked():
    agent = _agent([])
    r = _call(agent["agent_id"], "email.send_list", {"subject": "s", "body": "b"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "recipient_not_allowed"


@pytest.mark.parametrize("kind", ["research", "social"])
def test_list_send_is_outside_non_email_ceilings(kind):
    agent = _agent(kind=kind)
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/grants/email.send_list")
    assert r.status_code == 403


# --- runner ------------------------------------------------------------------------


def test_prompt_tells_the_model_its_list():
    agent = gateway.store.get_agent(_agent(LIST)["agent_id"])
    prompt = system_prompt(agent)
    assert "ana@example.gov" in prompt and "email_send_list" in prompt
    empty = gateway.store.get_agent(_agent([])["agent_id"])
    assert "mailing list is empty" in system_prompt(empty)
