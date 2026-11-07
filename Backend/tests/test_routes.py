import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)
SECRET = "test-secret-test-secret-test-secret"


def _user_token() -> str:
    claims = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "aud": "authenticated",
        "exp": int(time.time()) + 60,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_home_is_public():
    body = client.get("/api/v1/home").json()
    assert body["mission"] and body["buttons"]


def test_agent_routes_live_under_api_v1():
    assert client.get("/api/v1/agents").status_code == 200
    assert client.get("/api/v1/tools").status_code == 200
    assert client.get("/api/v1/atlas/meta").status_code == 200
    assert client.get("/api/v1/demo/matrix").json()["atlas_version"]
    # old root-level paths are gone
    assert client.get("/agents").status_code == 404


def test_health_not_shadowed_by_gateway():
    assert client.get("/api/v1/health").json() == {"status": "ok"}


@pytest.fixture
def admin_login_required():
    settings = get_settings()
    settings.agent_admin_requires_login = True
    yield
    settings.agent_admin_requires_login = False


def test_admin_routes_need_user_login_when_enabled(admin_login_required):
    assert client.get("/api/v1/agents").status_code == 401
    assert client.get("/api/v1/audit").status_code == 401
    assert client.post("/api/v1/auth/token", json={"agent_id": "agent-comms-01"}).status_code == 401
    assert (
        client.post("/api/v1/agents/agent-comms-01/grants/email.send/revoke").status_code == 401
    )

    headers = {"Authorization": f"Bearer {_user_token()}"}
    assert client.get("/api/v1/agents", headers=headers).status_code == 200


def test_agent_token_routes_stay_open_to_agents(admin_login_required):
    headers = {"Authorization": f"Bearer {_user_token()}"}
    token = client.post(
        "/api/v1/auth/token", json={"agent_id": "agent-comms-01"}, headers=headers
    ).json()["access_token"]

    # /mcp/call is authenticated by the agent token only, no user JWT needed.
    r = client.post(
        "/api/v1/mcp/call",
        json={"tool": "datagov.search", "arguments": {"q": "housing"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "allow"
