import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import ADMIN

from identity_gateway import agents_api as gateway  # noqa: E402
from identity_gateway.agent_types import AGENT_TYPES, disallowed_tools  # noqa: E402
from identity_gateway.dynamo_repo import agent_from_item, agent_to_item  # noqa: E402
from identity_gateway.store import AgentRecord  # noqa: E402
from mcp_tools.registry import TOOL_REGISTRY  # noqa: E402

# Every call signs in as an admin with our Cognito login (tests/helpers.py).
client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login")

OUTBOUND = {"email.send", "social.post", "social.reply"}


def _id() -> str:
    return f"agent-kind-{uuid.uuid4().hex[:8]}"


def _create(kind: str, **body):
    return client.post(
        "/api/v1/agents",
        json={"agent_id": _id(), "name": "Typed", "agency": "Dept.", "kind": kind, **body},
    )


# --- type definitions ---------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(AGENT_TYPES))
def test_type_defaults_are_registered_and_within_ceiling(kind):
    t = AGENT_TYPES[kind]
    assert t.default_tools <= set(TOOL_REGISTRY)
    if t.allowed_tools is not None:
        assert t.allowed_tools <= set(TOOL_REGISTRY)
        assert t.default_tools <= t.allowed_tools
    assert 1 <= t.default_max_steps <= 50


def test_research_agents_never_get_outbound_tools():
    # Research reads untrusted pages, so it must not be able to send or post.
    assert set(disallowed_tools("research", OUTBOUND)) == OUTBOUND


def test_unknown_kind_allows_nothing():
    assert disallowed_tools("bogus", ["web.scrape"]) == ["web.scrape"]


def test_agent_types_catalog_is_public():
    r = client.get("/api/v1/agent-types")
    assert r.status_code == 200
    by_kind = {t["kind"]: t for t in r.json()}
    assert set(by_kind) == {"email", "social", "research", "custom"}
    assert by_kind["custom"]["allowed_tools"] is None
    assert by_kind["research"]["allowed_tools"] == sorted(AGENT_TYPES["research"].allowed_tools)


# --- creation -------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["email", "social", "research"])
def test_create_fills_template_defaults(kind):
    r = _create(kind)
    assert r.status_code == 201
    body = r.json()
    t = AGENT_TYPES[kind]
    assert body["kind"] == kind
    assert body["tool_grants"] == sorted(t.default_tools)
    assert body["instructions"] == t.default_instructions
    assert body["max_steps"] == t.default_max_steps


def test_create_explicit_fields_override_template():
    body = _create("email", instructions="Custom.", tool_grants=[], max_steps=2).json()
    assert (body["instructions"], body["tool_grants"], body["max_steps"]) == ("Custom.", [], 2)


def test_kind_defaults_to_custom():
    body = client.post("/api/v1/agents", json={"name": "Old", "agency": "X"}).json()
    assert body["kind"] == "custom"


@pytest.mark.parametrize(
    "kind, tools",
    [
        ("research", ["web.scrape", "email.send"]),
        ("research", ["social.post"]),
        ("email", ["web.scrape"]),
        ("social", ["email.send"]),
    ],
)
def test_create_rejects_tools_outside_ceiling(kind, tools):
    r = _create(kind, tool_grants=tools)
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "tool_not_allowed_for_kind"


def test_create_rejects_unknown_kind():
    assert _create("hacker").status_code == 422


def test_kind_cannot_be_changed_by_patch():
    agent = _create("research").json()
    body = client.patch(f"/api/v1/agents/{agent['agent_id']}", json={"kind": "custom"}).json()
    assert body["kind"] == "research"


# --- grant changes ---------------------------------------------------------------


def test_grant_outside_ceiling_is_denied_and_audited():
    agent = _create("research").json()
    aid = agent["agent_id"]
    r = client.post(f"/api/v1/agents/{aid}/grants/email.send")
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "tool_not_allowed_for_kind"
    assert detail["audit_id"]
    assert client.get(f"/api/v1/agents/{aid}").json()["tool_grants"] == agent["tool_grants"]

    events = [e for e in client.get("/api/v1/audit").json() if e["id"] == detail["audit_id"]]
    assert events and events[0]["type"] == "grant_denied"


def test_grant_within_ceiling_is_allowed():
    agent = _create("email").json()
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/grants/datagov.search")
    assert r.status_code == 200
    assert r.json()["tool_grants"] == ["datagov.search", "email.send", "email.send_list"]


def test_replace_grants_outside_ceiling_changes_nothing():
    agent = _create("social").json()
    aid = agent["agent_id"]
    r = client.put(f"/api/v1/agents/{aid}/grants", json={"tools": ["social.post", "web.scrape"]})
    assert r.status_code == 403
    after = client.get(f"/api/v1/agents/{aid}").json()
    assert after["tool_grants"] == agent["tool_grants"]
    assert after["token_version"] == agent["token_version"]


def test_custom_agent_can_mix_tools():
    agent = _create("custom", tool_grants=["web.scrape"]).json()
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/grants/email.send")
    assert r.status_code == 200


# --- call time ----------------------------------------------------------------------


def test_call_time_check_blocks_grant_written_around_the_api():
    agent = _create("research").json()
    aid = agent["agent_id"]
    # Simulate a grant written straight to the table, bypassing the routes.
    gateway.store.grant_tool(aid, "email.send")
    token = client.post("/api/v1/auth/token", json={"agent_id": aid}).json()["access_token"]
    r = client.post(
        "/api/v1/agents/tool-call",
        json={"tool": "email.send", "arguments": {"to": "a@b.gov", "subject": "s", "body": "b"}},
        headers={"X-Agent-Token": token},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "tool_not_allowed_for_kind"

    ok = client.post(
        "/api/v1/agents/tool-call",
        json={"tool": "web.scrape", "arguments": {"url": "https://example.gov"}},
        headers={"X-Agent-Token": token},
    )
    assert ok.status_code == 200


# --- persistence ----------------------------------------------------------------------


def test_kind_round_trips_through_dynamo_item():
    agent = AgentRecord(_id(), "A", "B", {"web.scrape"}, kind="research")
    assert agent_from_item(agent_to_item(agent)).kind == "research"


def test_legacy_dynamo_item_without_kind_is_custom():
    item = agent_to_item(AgentRecord(_id(), "A", "B", set()))
    del item["kind"]
    assert agent_from_item(item).kind == "custom"
