"""research_then_email: research agent finds facts, email agent uses them."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_runner import FakeModel, response, text, tool_use, use_model  # noqa: F401

from identity_gateway import app as gateway
from identity_gateway.pipeline import EMAIL_TASK, RESEARCH_TASK, execute_research_then_email
from identity_gateway.runner import AgentRunner, RunnerConfig, new_run
from identity_gateway.store import AgentRecord

client = TestClient(app)


def _id() -> str:
    return f"agent-pipe-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _reset_runs():
    gateway.runs._runs.clear()
    yield
    gateway.runs._runs.clear()


def test_research_first_rejects_non_research_agent(use_model):
    use_model(response(text("noop")))
    r = client.post(
        "/api/v1/agents",
        json={"name": "Solo", "agency": "City", "kind": "email", "tool_grants": ["email.send"]},
    )
    assert r.status_code == 201
    aid = r.json()["agent_id"]
    out = client.post(
        f"/api/v1/agents/{aid}/runs",
        json={"task": "Email everyone a fact", "research_first": True, "research_agent_id": aid},
    )
    assert out.status_code == 400
    assert "research" in out.json()["detail"].lower()


def test_execute_research_then_email_handoff(use_model):
    research = AgentRecord(
        _id(),
        "Research",
        "City",
        {"datagov.search"},
        kind="research",
        instructions="Research.",
        max_steps=4,
    )
    email = AgentRecord(
        _id(),
        "Email",
        "City",
        {"email.send_list"},
        kind="email",
        instructions="Email.",
        recipients=["a@example.gov"],
        max_steps=4,
    )
    gateway.store._agents.insert(research)
    gateway.store._agents.insert(email)

    fake = use_model(
        # research: search then summarize
        response(tool_use("datagov_search", {"q": "santa clara population 2023"}), stop="tool_use"),
        response(text("Population of Santa Clara city was 131,364 in 2023 (Census Vintage 2023).")),
        # email: queue for approval
        response(
            tool_use(
                "email_send_list",
                {"subject": "Population", "body": "Santa Clara city population in 2023 was 131,364."},
            ),
            stop="tool_use",
        ),
        response(text("Queued the email for approval.")),
    )

    parent = new_run(email, "Email everyone the population of Santa Clara City in 2023.", "anthropic.claude-opus-5-5", None)
    gateway.runs.add(parent)

    execute_research_then_email(
        gateway.runner,
        parent,
        email,
        research,
        add_run=gateway.runs.add,
    )

    assert parent.pipeline == "research_then_email"
    assert parent.research_run_id
    assert parent.research_output and "131,364" in parent.research_output
    assert parent.status == "completed"
    assert any(s.get("type") == "pipeline" and s.get("stage") == "handoff" for s in parent.steps)
    assert any(s.get("pipeline_stage") == "research" and s.get("tool") == "datagov.search" for s in parent.steps)
    assert any(s.get("pipeline_stage") == "email" for s in parent.steps)
    # Email phase task included research findings.
    email_user_msgs = [c["messages"][0]["content"] for c in fake.calls if c["messages"][0]["role"] == "user"]
    assert any("131,364" in (m if isinstance(m, str) else "") for m in email_user_msgs)
    assert RESEARCH_TASK.split("{task}")[0][:20] in fake.calls[0]["messages"][0]["content"]
    assert "Research findings" in EMAIL_TASK
