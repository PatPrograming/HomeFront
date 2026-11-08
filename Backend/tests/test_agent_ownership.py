import pytest

pytest.skip(
    "Not ported in the origin/main merge: tests ownership of main's /agents, grant and /auth/token routes, which are deliberately not reachable on this branch (mandate broker + Cedar instead; see test_gateway_api.py).",
    allow_module_level=True,
)

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from tests.helpers import OTHER_KEY, auth

client = TestClient(app)

ALICE = "00000000-0000-0000-0000-00000000a11c"
BOB = "00000000-0000-0000-0000-000000000b0b"


def _auth(sub: str, **kwargs) -> dict[str, str]:
    return auth(sub, **kwargs)


def _create_as(headers: dict[str, str] | None, **overrides) -> dict:
    body = {
        "agent_id": f"agent-own-{uuid.uuid4().hex[:8]}",
        "name": "Owned Agent",
        "agency": "Dept. of Testing",
        "tool_grants": ["web.scrape", "datagov.search"],
        **overrides,
    }
    r = client.post("/api/v1/agents", json=body, headers=headers or {})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def login_required():
    settings = get_settings()
    settings.agent_admin_requires_login = True
    yield
    settings.agent_admin_requires_login = False


def test_created_agent_is_owned_by_caller():
    agent = _create_as(_auth(ALICE))
    assert agent["owner_id"] == ALICE


def test_owner_can_manage_own_agent():
    alice = _auth(ALICE)
    aid = _create_as(alice)["agent_id"]
    assert client.get(f"/api/v1/agents/{aid}", headers=alice).status_code == 200
    assert client.patch(f"/api/v1/agents/{aid}", json={"name": "x"}, headers=alice).status_code == 200
    assert client.post(f"/api/v1/agents/{aid}/grants/email.send", headers=alice).status_code == 200
    assert (
        client.post(f"/api/v1/agents/{aid}/grants/email.send/revoke", headers=alice).status_code
        == 200
    )
    r = client.put(f"/api/v1/agents/{aid}/grants", json={"tools": []}, headers=alice)
    assert r.status_code == 200
    r = client.post("/api/v1/auth/token", json={"agent_id": aid}, headers=alice)
    assert r.status_code == 200


@pytest.mark.parametrize("who", ["bob", "anonymous"])
def test_others_cannot_see_or_touch_owned_agent(who):
    alice = _auth(ALICE)
    agent = _create_as(alice)
    aid = agent["agent_id"]
    headers = _auth(BOB) if who == "bob" else {}

    listed = {a["agent_id"] for a in client.get("/api/v1/agents", headers=headers).json()}
    assert aid not in listed

    attempts = [
        client.get(f"/api/v1/agents/{aid}", headers=headers),
        client.patch(f"/api/v1/agents/{aid}", json={"instructions": "pwned"}, headers=headers),
        client.post(f"/api/v1/agents/{aid}/grants/email.send", headers=headers),
        client.post(f"/api/v1/agents/{aid}/grants/web.scrape/revoke", headers=headers),
        client.put(f"/api/v1/agents/{aid}/grants", json={"tools": ["email.send"]}, headers=headers),
        client.post("/api/v1/auth/token", json={"agent_id": aid}, headers=headers),
    ]
    # 404 (not 403) so the agent's existence isn't revealed.
    assert [r.status_code for r in attempts] == [404] * len(attempts)

    # Nothing changed.
    after = client.get(f"/api/v1/agents/{aid}", headers=alice).json()
    assert after == agent


def test_audit_is_scoped_to_visible_agents():
    alice, bob = _auth(ALICE), _auth(BOB)
    aid = _create_as(alice)["agent_id"]
    client.post(f"/api/v1/agents/{aid}/grants/web.scrape/revoke", headers=alice)

    def audited(headers):
        return {e["agent_id"] for e in client.get("/api/v1/audit", headers=headers).json()}

    assert aid in audited(alice)
    assert aid not in audited(bob)
    assert aid not in audited({})


def test_shared_demo_agent_stays_usable_by_everyone():
    for headers in ({}, _auth(ALICE), _auth(BOB)):
        listed = {a["agent_id"] for a in client.get("/api/v1/agents", headers=headers).json()}
        assert "agent-comms-01" in listed
        r = client.post("/api/v1/auth/token", json={"agent_id": "agent-comms-01"}, headers=headers)
        assert r.status_code == 200


def test_anonymous_creates_ownerless_agent_when_login_optional():
    assert _create_as(None)["owner_id"] is None


def test_anonymous_rejected_when_login_required(login_required):
    r = client.post("/api/v1/agents", json={"name": "x", "agency": "y"})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic Zm9vOmJhcg=="},
        {"Authorization": "Bearer "},
        _auth(ALICE, key=OTHER_KEY),
        _auth(ALICE, exp_in=-60),
    ],
    ids=["garbage", "basic", "empty", "bad-signature", "expired"],
)
def test_bad_user_token_is_401_not_anonymous(headers):
    # Even with login optional, a bad token must not fall back to anonymous.
    assert client.get("/api/v1/agents", headers=headers).status_code == 401
    r = client.post("/api/v1/agents", json={"name": "x", "agency": "y"}, headers=headers)
    assert r.status_code == 401


def test_owner_cannot_be_set_by_request_body():
    agent = _create_as(_auth(ALICE), owner_id=BOB)
    assert agent["owner_id"] == ALICE
    agent = client.patch(
        f"/api/v1/agents/{agent['agent_id']}", json={"owner_id": BOB}, headers=_auth(ALICE)
    ).json()
    assert agent["owner_id"] == ALICE


def test_agent_token_route_unaffected_by_user_auth():
    # /mcp/call is authenticated by the *agent* token; the user resolver must
    # not try to parse it as a Cognito JWT.
    alice = _auth(ALICE)
    aid = _create_as(alice)["agent_id"]
    token = client.post("/api/v1/auth/token", json={"agent_id": aid}, headers=alice).json()[
        "access_token"
    ]
    r = client.post(
        "/api/v1/mcp/call",
        json={"tool": "web.scrape", "arguments": {"url": "https://example.gov"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()[
        "owner_id"
    ] == ALICE
