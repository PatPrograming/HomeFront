import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app  # also puts packages/*/src on sys.path

from identity_gateway.store import (  # noqa: E402
    AgentRecord,
    DuplicateAgentError,
    GatewayStore,
    MemoryAgentRepository,
)
from identity_gateway.supabase_repo import (  # noqa: E402
    SupabaseAgentRepository,
    agent_from_row,
    agent_to_row,
)
from postgrest.exceptions import APIError  # noqa: E402

client = TestClient(app)


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
    assert created["owner_id"] is None

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
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["instructions"] == agent["instructions"]

    client.post(f"/api/v1/agents/{aid}/grants/datagov.search/revoke")
    assert client.get(f"/api/v1/agents/{aid}").json()["tool_grants"] == ["web.scrape"]

    r = client.post(
        "/api/v1/mcp/call", json={"tool": "datagov.search", "arguments": {}}, headers=headers
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


# --- Supabase repository (fake client, no network) --------------------------


class FakeQuery:
    """Minimal stand-in for the postgrest query builder over a list of rows."""

    def __init__(self, rows: list[dict], error: APIError | None = None):
        self.rows = rows
        self.error = error
        self._op = "select"
        self._payload = None
        self._filters: list[tuple[str, object]] = []
        self._count = False

    def select(self, *_cols, count=None, head=None):
        self._op, self._count = "select", bool(count)
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._op, self._payload = "update", payload
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def _match(self):
        return [r for r in self.rows if all(r.get(c) == v for c, v in self._filters)]

    def execute(self):
        if self.error:
            raise self.error
        stamp = {"created_at": "2026-11-07T12:00:00.123456+00:00"}
        if self._op == "insert":
            row = {**self._payload, **stamp}
            self.rows.append(row)
            return SimpleNamespace(data=[row], count=None)
        if self._op == "update":
            hits = self._match()
            for r in hits:
                r.update(self._payload)
            return SimpleNamespace(data=hits, count=None)
        hits = self._match()
        return SimpleNamespace(data=hits, count=len(hits) if self._count else None)


class FakeClient:
    def __init__(self, error: APIError | None = None):
        self.rows: list[dict] = []
        self.error = error

    def table(self, name):
        assert name == "agents"
        return FakeQuery(self.rows, self.error)


def test_row_round_trip():
    agent = AgentRecord(
        agent_id="agent-rt-01",
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
    row = agent_to_row(agent)
    assert row["tool_grants"] == ["datagov.search", "web.scrape"]
    back = agent_from_row({**row, "created_at": "2026-11-07T12:00:00+00:00"})
    assert back.created_at > 0
    assert back == AgentRecord(**{**agent.__dict__, "created_at": back.created_at})


def test_supabase_repository_crud():
    repo = SupabaseAgentRepository(FakeClient())
    store = GatewayStore(repo)
    store.create_agent("A", "B", ["web.scrape"], "agent-sb-01", instructions="hi")
    assert repo.count() == 1
    assert store.get_agent("agent-sb-01").instructions == "hi"

    store.grant_tool("agent-sb-01", "datagov.search")
    agent = store.get_agent("agent-sb-01")
    assert agent.tool_grants == {"web.scrape", "datagov.search"}
    assert agent.token_version == 2

    with pytest.raises(KeyError):
        store.grant_tool("agent-missing", "web.scrape")


def test_supabase_repository_maps_unique_violation():
    err = APIError({"code": "23505", "message": "duplicate key", "details": "", "hint": ""})
    repo = SupabaseAgentRepository(FakeClient(error=err))
    with pytest.raises(DuplicateAgentError):
        repo.insert(AgentRecord("agent-dup-01", "A", "B", set()))
