"""Page-shaped read endpoints: Agents overview and Monitoring, plus the
timestamps and token usage the Activity page needs."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.helpers import ADMIN
from tests.test_runner import response, text, tool_use, use_model  # noqa: F401 (fixture)

client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login")


def _agent(**overrides):
    body = {
        "agent_id": f"agent-page-{uuid.uuid4().hex[:8]}",
        "name": "Page Agent",
        "agency": "Dept. of Pages",
        "kind": "research",
        "instructions": "Research public data.",
        **overrides,
    }
    r = client.post("/api/v1/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_overview_is_a_small_summary_and_not_read_as_an_agent_id():
    agent = _agent()
    r = client.get("/api/v1/agents/overview")
    assert r.status_code == 200
    row = next(a for a in r.json() if a["agent_id"] == agent["agent_id"])
    assert row["role"] == "Research"
    assert row["purpose"] == "Research public data."
    assert row["active"] is True and row["pending_approvals"] == 0
    assert row["last_run"] is None
    # Heavy fields stay out of the overview.
    assert "instructions" not in row and "email_settings" not in row and "recipients" not in row


def test_monitoring_has_identity_calls_last_result_and_atlas():
    agent = _agent(tool_grants=["datagov.search"])
    aid = agent["agent_id"]
    token = client.post("/api/v1/auth/token", json={"agent_id": aid}).json()
    headers = {"X-Agent-Token": token["access_token"]}
    client.post("/api/v1/agents/tool-call", json={"tool": "datagov.search", "arguments": {"q": "housing"}}, headers=headers)
    client.post("/api/v1/agents/tool-call", json={"tool": "web.scrape", "arguments": {"url": "https://x.gov"}}, headers=headers)

    r = client.get(f"/api/v1/agents/{aid}/monitoring")
    assert r.status_code == 200, r.text
    data = r.json()

    identity = data["identity"]
    assert identity["token_version"] == agent["token_version"]
    assert [s["session_id"] for s in identity["live_sessions"]] == [token["session_id"]]
    assert token["access_token"] not in r.text  # token values never leave the gateway

    calls = data["tool_calls"]
    assert [(c["tool"], c["decision"]) for c in calls] == [("web.scrape", "deny"), ("datagov.search", "allow")]
    assert all(isinstance(c["ts"], float) for c in calls)

    last = data["last_tool_result"]
    assert last["tool"] == "web.scrape" and last["decision"] == "deny"

    assert data["atlas"]["techniques"] and data["atlas"]["mitigations"]
    assert any(e["type"] == "authenticate" for e in data["audit"])


def test_monitoring_unknown_agent_is_404():
    assert client.get("/api/v1/agents/no-such-agent/monitoring").status_code == 404


def test_run_logs_token_usage_and_step_times(use_model):  # noqa: F811
    use_model(
        response(tool_use("datagov_search", {"q": "housing"}), stop="tool_use"),
        response(text("done")),
    )
    agent = _agent(tool_grants=["datagov.search"])
    aid = agent["agent_id"]
    run_id = client.post(f"/api/v1/agents/{aid}/runs", json={"task": "go"}).json()["run_id"]

    run = client.get(f"/api/v1/agents/{aid}/runs/{run_id}").json()
    assert isinstance(run["steps"][0]["ts"], float)

    [finished] = client.get("/api/v1/audit", params={"agent_id": aid, "type": "run_finished"}).json()
    assert finished["usage"] == {"input_tokens": 200, "output_tokens": 40}
    assert finished["model"] == run["model"]

    data = client.get(f"/api/v1/agents/{aid}/monitoring").json()
    assert data["recent_runs"][0]["usage"] == {"input_tokens": 200, "output_tokens": 40}
    overview = next(a for a in client.get("/api/v1/agents/overview").json() if a["agent_id"] == aid)
    assert overview["last_run"]["status"] == "completed" and overview["run_count"] == 1
