import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # also puts packages/*/src on sys.path
from tests.helpers import ADMIN, ADMIN_USER

from identity_gateway.store import (  # noqa: E402
    AgentRecord,
    DuplicateAgentError,
    GatewayStore,
    MemoryAgentRepository,
)
from identity_gateway.dynamo_repo import (  # noqa: E402
    DynamoAgentRepository,
    agent_from_item,
    agent_to_item,
    seed_demo_agent,
)

from app.core.dynamodb import table  # noqa: E402

# Every call signs in as an admin with our Cognito login (tests/helpers.py).
client = TestClient(app, headers=ADMIN)
pytestmark = pytest.mark.usefixtures("cognito_login", "old_tables")


def _new_id() -> str:
    return f"agent-test-{uuid.uuid4().hex[:8]}"


def _create(**overrides):
    body = {
        "agent_id": _new_id(),
        "name": "Housing Researcher",
        "agency": "Dept. of Housing",
        "instructions": "Find datasets about affordable housing and summarize them.",
        "model": "claude-sonnet-5-5",
        "max_steps": 6,
        "tool_grants": ["datagov.search", "web.scrape"],
        **overrides,
    }
    return client.post("/api/v1/agents", json=body)


# --- API ------------------------------------------------------------------


def test_create_agent_stores_full_definition():
    r = _create()
    assert r.status_code == 201
    created = r.json()
    assert created["instructions"].startswith("Find datasets")
    assert created["model"] == "claude-sonnet-5-5"
    assert created["max_steps"] == 6
    assert created["tool_grants"] == ["datagov.search", "web.scrape"]
    assert created["owner_id"] == ADMIN_USER

    fetched = client.get(f"/api/v1/agents/{created['agent_id']}").json()
    assert fetched == created
    listed = {a["agent_id"]: a for a in client.get("/api/v1/agents").json()}
    assert listed[created["agent_id"]] == created


def test_create_agent_defaults():
    r = client.post("/api/v1/agents", json={"name": "Minimal", "agency": "X"})
    assert r.status_code == 201
    body = r.json()
    assert body["agent_id"].startswith("agent-")
    assert body["instructions"] == ""
    assert body["model"] is None
    assert body["max_steps"] == 10
    assert body["tool_grants"] == []


def test_duplicate_agent_id_is_rejected_not_overwritten():
    first = _create().json()
    r = _create(agent_id=first["agent_id"], name="Impostor")
    assert r.status_code == 409
    assert client.get(f"/api/v1/agents/{first['agent_id']}").json()["name"] == first["name"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_id": "Bad ID!"},
        {"agent_id": "ab"},
        {"name": ""},
        {"max_steps": 0},
        {"max_steps": 51},
        {"instructions": "x" * 8001},
    ],
)
def test_create_agent_validation(overrides):
    assert _create(**overrides).status_code == 422


def test_create_agent_unknown_tool():
    assert _create(tool_grants=["shell.exec"]).status_code == 400


def test_get_unknown_agent_404():
    assert client.get("/api/v1/agents/agent-does-not-exist").status_code == 404


def test_patch_agent_updates_only_sent_fields():
    agent = _create().json()
    r = client.patch(
        f"/api/v1/agents/{agent['agent_id']}",
        json={"instructions": "New purpose.", "max_steps": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["instructions"] == "New purpose."
    assert body["max_steps"] == 3
    assert body["name"] == agent["name"]
    assert body["model"] == agent["model"]
    assert body["token_version"] == agent["token_version"]


def test_patch_model_null_resets_to_default():
    agent = _create().json()
    body = client.patch(f"/api/v1/agents/{agent['agent_id']}", json={"model": None}).json()
    assert body["model"] is None


def test_patch_cannot_change_grants_or_unknown_agent():
    agent = _create().json()
    body = client.patch(
        f"/api/v1/agents/{agent['agent_id']}", json={"tool_grants": ["email.send"]}
    ).json()
    assert body["tool_grants"] == agent["tool_grants"]
    assert client.patch("/api/v1/agents/agent-nope-123", json={"name": "x"}).status_code == 404


def test_inactive_agent_cannot_get_token():
    agent = _create().json()
    client.patch(f"/api/v1/agents/{agent['agent_id']}", json={"active": False})
    r = client.post("/api/v1/auth/token", json={"agent_id": agent["agent_id"]})
    assert r.status_code == 403


def test_revoke_persists_and_blocks_next_call():
    agent = _create().json()
    aid = agent["agent_id"]
    token = client.post("/api/v1/auth/token", json={"agent_id": aid}).json()["access_token"]
    headers = {"X-Agent-Token": token}

    # /auth/me (agent token only) is not served: every route needs our staff login.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 404

    client.post(f"/api/v1/agents/{aid}/grants/datagov.search/revoke")
    assert client.get(f"/api/v1/agents/{aid}").json()["tool_grants"] == ["web.scrape"]

    r = client.post(
        "/api/v1/agents/tool-call", json={"tool": "datagov.search", "arguments": {}}, headers=headers
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "tool_not_granted"


# --- store ----------------------------------------------------------------


def test_memory_repository_returns_copies():
    repo = MemoryAgentRepository(seed=False)
    repo.insert(AgentRecord("agent-abc", "A", "B", {"web.scrape"}))
    got = repo.get("agent-abc")
    got.tool_grants.add("email.send")
    assert repo.get("agent-abc").tool_grants == {"web.scrape"}


def test_store_update_rejects_non_editable_fields():
    store = GatewayStore(MemoryAgentRepository())
    with pytest.raises(ValueError):
        store.update_agent("agent-comms-01", token_version=99)


# --- DynamoDB repository (moto) ---------------------------------------------


def _repo() -> DynamoAgentRepository:
    return DynamoAgentRepository(table("agents"))


def test_item_round_trip():
    agent = AgentRecord(
        agent_id=_new_id(),
        name="RT",
        agency="Agency",
        tool_grants={"web.scrape", "datagov.search"},
        instructions="Do things.",
        model=None,
        max_steps=4,
        owner_id="00000000-0000-0000-0000-000000000001",
        token_version=3,
        active=False,
    )
    item = agent_to_item(agent)
    assert item["tool_grants"] == ["datagov.search", "web.scrape"]
    back = agent_from_item(item)
    assert back.created_at == pytest.approx(agent.created_at, abs=1e-3)
    assert back == AgentRecord(**{**agent.__dict__, "created_at": back.created_at})


def test_dynamo_repository_crud():
    repo = _repo()
    store = GatewayStore(repo)
    aid = _new_id()
    before = repo.count()
    store.create_agent("A", "B", ["web.scrape"], aid, instructions="hi", owner_id="u-1")
    assert repo.count() == before + 1

    got = store.get_agent(aid)
    assert (got.instructions, got.owner_id, got.max_steps) == ("hi", "u-1", 10)
    assert isinstance(got.max_steps, int) and isinstance(got.token_version, int)

    store.grant_tool(aid, "datagov.search")
    agent = store.get_agent(aid)
    assert agent.tool_grants == {"web.scrape", "datagov.search"}
    assert agent.token_version == 2

    store.revoke_all(aid)
    assert store.get_agent(aid).tool_grants == set()  # empty list survives the round trip
    assert aid in {a.agent_id for a in store.list_agents()}

    with pytest.raises(KeyError):
        store.grant_tool("agent-missing-x", "web.scrape")


def test_dynamo_repository_rejects_duplicate_id():
    repo = _repo()
    aid = _new_id()
    repo.insert(AgentRecord(aid, "A", "B", set()))
    with pytest.raises(DuplicateAgentError):
        repo.insert(AgentRecord(aid, "Impostor", "B", set()))
    assert repo.get(aid).name == "A"


def test_dynamo_save_does_not_resurrect_missing_agent():
    with pytest.raises(KeyError):
        _repo().save(AgentRecord(_new_id(), "A", "B", set()))


def test_seed_demo_agent_is_idempotent():
    repo = _repo()
    seed_demo_agent(repo)
    assert seed_demo_agent(repo) is False
    demo = repo.get("agent-comms-01")
    assert demo.owner_id is None and "email.send" in demo.tool_grants


def test_dynamo_list_paginates():
    repo = _repo()
    ids = {_new_id() for _ in range(5)}
    for aid in ids:
        repo.insert(AgentRecord(aid, "A", "B", set()))

    # Force tiny pages to exercise LastEvaluatedKey handling.
    real_scan = repo._table.scan
    repo._table = type("T", (), {})()
    repo._table.scan = lambda **kw: real_scan(Limit=2, **kw)
    assert ids <= {a.agent_id for a in repo.list()}
    assert repo.count() >= 5


def test_api_against_dynamo_store():
    """The full agent API, owner scoping included, on the DynamoDB repository."""
    from identity_gateway import agents_api as gateway

    from tests.helpers import auth

    saved = gateway.store._agents
    gateway.store.use_agent_repository(_repo())
    try:
        alice = auth("00000000-0000-0000-0000-00000000a11c")
        r = client.post("/api/v1/agents", json={"name": "Dyn", "agency": "X", "tool_grants": ["web.scrape"]}, headers=alice)
        assert r.status_code == 201
        aid = r.json()["agent_id"]
        assert client.get(f"/api/v1/agents/{aid}", headers=alice).json()["owner_id"].endswith("a11c")
        assert client.get(f"/api/v1/agents/{aid}", headers=auth()).status_code == 404
        assert client.post(f"/api/v1/agents/{aid}/grants/web.scrape/revoke", headers=alice).status_code == 200
        assert client.get(f"/api/v1/agents/{aid}", headers=alice).json()["tool_grants"] == []
    finally:
        gateway.store.use_agent_repository(saved)


# --- deleting agents -------------------------------------------------------------------


def _mine(sub, **body):
    from tests.helpers import auth as _auth

    r = client.post(
        "/api/v1/agents",
        json={"agent_id": f"agent-del-{uuid.uuid4().hex[:8]}", "name": "Del", "agency": "City", "kind": "social", **body},
        headers=_auth(sub),
    )
    assert r.status_code == 201, r.text
    return r.json()["agent_id"], _auth(sub)


def test_owner_deletes_agent_and_it_is_gone():
    aid, headers = _mine("00000000-0000-0000-0000-00000000de11")
    r = client.delete(f"/api/v1/agents/{aid}", headers=headers)
    assert r.status_code == 200 and r.json()["deleted"] == aid
    assert client.get(f"/api/v1/agents/{aid}", headers=headers).status_code == 404
    assert aid not in [a["agent_id"] for a in client.get("/api/v1/agents", headers=headers).json()]
    assert client.delete(f"/api/v1/agents/{aid}", headers=headers).status_code == 404


def test_other_users_cannot_delete_it():
    from tests.helpers import auth as _auth

    aid, headers = _mine("00000000-0000-0000-0000-00000000de12")
    assert client.delete(f"/api/v1/agents/{aid}", headers=_auth("00000000-0000-0000-0000-00000000de13")).status_code == 404
    assert client.get(f"/api/v1/agents/{aid}", headers=headers).status_code == 200


def test_shared_demo_agents_cannot_be_deleted():
    r = client.delete("/api/v1/agents/agent-comms-01")
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "cannot_delete_shared_agent"
    assert client.get("/api/v1/agents/agent-comms-01").status_code == 200


def test_deleting_rejects_pending_approvals_and_kills_tokens():
    sub = "00000000-0000-0000-0000-00000000de14"
    aid, headers = _mine(sub)
    token = client.post("/api/v1/auth/token", json={"agent_id": aid}, headers=headers).json()["access_token"]
    agent_headers = {**headers, "X-Agent-Token": token}
    queued = client.post(
        "/api/v1/agents/tool-call", json={"tool": "social.post", "arguments": {"platform": "x", "text": "hi"}}, headers=agent_headers
    ).json()
    assert queued.get("decision") == "pending_approval", queued

    r = client.delete(f"/api/v1/agents/{aid}", headers=headers)
    assert r.json()["rejected_approvals"] == 1
    assert client.post(
        "/api/v1/agents/tool-call", json={"tool": "social.post", "arguments": {"platform": "x", "text": "hi"}}, headers=agent_headers
    ).status_code in (401, 403, 404)
