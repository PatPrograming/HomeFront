"""Plan card: the assistant shows which helper agents it will use; only the session's own staffer decides it."""

from __future__ import annotations

from .test_assistant_api import SID, agent_headers, as_staff, session  # noqa: F401 (fixtures)
from .test_gateway_api import CHIEF, MAYA, _env, client, gateway, store, table  # noqa: F401 (fixtures)

STEPS = [
    {"agent": "research", "purpose": "find facts", "tools": ["datagov.search", "web_scrape"]},
    {"agent": "social", "purpose": "write one X post draft", "tools": ["social_post"]},
]


def _propose(client, steps=STEPS):
    response = client.post("/api/v1/agent/plans", json={"steps": steps}, headers=agent_headers())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_approval"
    return response.json()


def test_card_drops_a_tool_outside_the_staffers_role(client, session):
    # Maya is a caseworker: research tools yes, social_post no.
    plan = _propose(client)
    research, social = plan["steps"]
    assert [t["name"] for t in research["tools"]] == ["datagov_search", "web_scrape"]
    assert research["readOnly"] is True and research["dropped"] == []
    assert social["tools"] == [] and social["dropped"] == ["social_post"]
    as_staff()
    card = client.get(f"/api/v1/sessions/{SID}/messages").json()["messages"][-1]
    assert card["role"] == "assistant" and card["plan"]["id"] == plan["plan_id"] and card["plan"]["status"] == "pending"


def test_only_the_owner_can_decide_and_only_once(client, session):
    plan_id = _propose(client)["plan_id"]
    url = f"/api/v1/sessions/{SID}/plans/{plan_id}"
    as_staff(CHIEF)
    assert client.post(url, json={"decision": "approve"}).status_code == 404
    as_staff(MAYA)
    decided = client.post(url, json={"decision": "approve"})
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    assert client.post(url, json={"decision": "cancel"}).status_code == 409
    messages = client.get(f"/api/v1/sessions/{SID}/messages").json()["messages"]
    assert (messages[-1]["role"], messages[-1]["text"]) == ("user", "Approved the plan.")
    assert next(m for m in messages if m.get("plan"))["plan"]["status"] == "approved"


def test_cancel_posts_cancelled_and_is_audited(client, session, gateway):
    plan_id = _propose(client)["plan_id"]
    as_staff()
    assert client.post(f"/api/v1/sessions/{SID}/plans/{plan_id}", json={"decision": "cancel"}).json()["status"] == "cancelled"
    messages = client.get(f"/api/v1/sessions/{SID}/messages").json()["messages"]
    assert messages[-1]["text"] == "Cancelled the plan."
    events = gateway.store.events_since(None)[0]
    assert any(e.tool == "plan_decide" and e.reason == "cancelled by staff" for e in events)

