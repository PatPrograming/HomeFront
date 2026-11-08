"""Taint tracking: after a run/session executes an untrusted_input tool, its
outbound tools (email, social) are denied, whatever the agent's type."""
import uuid
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path

from identity_gateway import app as gateway  # noqa: E402
from identity_gateway.runner import AgentRunner  # noqa: E402
from mcp_tools.registry import TOOL_REGISTRY  # noqa: E402

client = TestClient(app)

TO = "resident@example.gov"
EMAIL = {"to": TO, "subject": "Update", "body": "Town hall Tuesday."}


def _agent(**overrides):
    body = {
        "agent_id": f"agent-taint-{uuid.uuid4().hex[:8]}",
        "name": "Comms",
        "agency": "Dept.",
        "kind": "custom",
        "tool_grants": ["web.scrape", "email.send", "social.post", "datagov.search"],
        "recipients": [TO],
        **overrides,
    }
    r = client.post("/api/v1/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _session(agent_id):
    body = client.post("/api/v1/auth/token", json={"agent_id": agent_id}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["session_id"]


def _call(headers, tool, arguments):
    return client.post("/api/v1/mcp/call", json={"tool": tool, "arguments": arguments}, headers=headers)


def test_outbound_flag_marks_email_and_social():
    outbound = {name for name, t in TOOL_REGISTRY.items() if t.outbound}
    assert outbound == {"email.send", "email.send_list", "social.post", "social.reply"}
    assert client.get("/api/v1/tools").json()[0].get("outbound") is not None


def test_scrape_then_email_is_denied_as_tainted():
    agent = _agent()
    headers, session = _session(agent["agent_id"])
    assert _call(headers, "web.scrape", {"url": "https://example.gov"}).status_code == 200

    r = _call(headers, "email.send", EMAIL)
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "tainted_session"
    assert detail["tainted_by"] == "web.scrape"
    assert "AML.T0051.001" in detail["atlas"]["technique_ids"]
    assert "AML.M0030" in detail["atlas"]["mitigation_ids"]

    # Social is outbound too; reading tools still work.
    assert _call(headers, "social.post", {"platform": "x", "text": "hi"}).json()["detail"]["error"] == "tainted_session"
    assert _call(headers, "datagov.search", {"q": "flood"}).status_code == 200

    # Audited under the session, with ATLAS labels; nothing was queued.
    events = client.get("/api/v1/audit", params={"agent_id": agent["agent_id"], "trace_id": session}).json()
    [denied] = [e for e in events if e["id"] == detail["audit_id"]]
    assert denied["reason"] == "tainted_session" and denied["tool"] == "email.send"
    assert denied["atlas"]["technique_ids"] == ["AML.T0051.001", "AML.T0086"]
    assert not [a for a in client.get("/api/v1/approvals").json() if a["agent"]["agent_id"] == agent["agent_id"]]


def test_email_before_scrape_goes_to_approval_and_can_be_approved():
    agent = _agent()
    headers, _ = _session(agent["agent_id"])
    first = _call(headers, "email.send", EMAIL)
    assert first.status_code == 200 and first.json()["decision"] == "pending_approval"
    assert _call(headers, "web.scrape", {"url": "https://example.gov"}).status_code == 200

    # Approval isn't re-checked against later taint: the arguments predate it.
    approved = client.post(f"/api/v1/approvals/{first.json()['approval_id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"

    # But a new outbound call in this session is now blocked.
    assert _call(headers, "email.send", EMAIL).json()["detail"]["error"] == "tainted_session"


def test_separate_session_is_not_tainted():
    agent = _agent()
    tainted, _ = _session(agent["agent_id"])
    _call(tainted, "web.scrape", {"url": "https://example.gov"})
    clean, _ = _session(agent["agent_id"])
    r = _call(clean, "email.send", EMAIL)
    assert r.status_code == 200 and r.json()["decision"] == "pending_approval"


def test_denied_tool_call_does_not_taint():
    agent = _agent(tool_grants=["email.send"])
    headers, _ = _session(agent["agent_id"])
    assert _call(headers, "web.scrape", {"url": "https://example.gov"}).status_code == 403  # not granted
    assert _call(headers, "email.send", EMAIL).json()["decision"] == "pending_approval"


def _run_with(script, agent_id):
    calls = []

    def create(**params):
        calls.append(params)
        return script.pop(0)

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    previous = gateway.runner
    gateway.configure_runner(
        AgentRunner(client_factory=lambda: fake, call_tool=gateway.gateway_tool_call, issue_token=gateway._issue_run_token)
    )
    try:
        r = client.post(f"/api/v1/agents/{agent_id}/runs", json={"task": "Post the notice"})
        return client.get(f"/api/v1/agents/{agent_id}/runs/{r.json()['run_id']}").json()
    finally:
        gateway.configure_runner(previous)


def _tool_turn(name, inp):
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", id=f"t_{uuid.uuid4().hex[:6]}", name=name, input=inp)],
        stop_reason="tool_use",
        usage=None,
    )


def test_runner_scrape_then_email_is_denied_and_next_run_is_clean():
    agent = _agent()
    done = SimpleNamespace(content=[SimpleNamespace(type="text", text="done")], stop_reason="end_turn", usage=None)
    run = _run_with(
        [_tool_turn("web_scrape", {"url": "https://example.gov"}), _tool_turn("email_send", EMAIL), done],
        agent["agent_id"],
    )
    assert run["status"] == "completed"
    assert [(s["tool"], s["decision"]) for s in run["steps"]] == [("web.scrape", "allow"), ("email.send", "deny")]
    assert run["steps"][1]["error"] == "tainted_session"

    # A new run starts clean.
    run2 = _run_with([_tool_turn("email_send", EMAIL), done], agent["agent_id"])
    assert run2["steps"][0]["decision"] == "pending_approval"
