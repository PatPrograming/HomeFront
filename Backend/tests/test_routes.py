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
    assert client.get("/api/v1/tools").status_code == 200
    assert client.get("/api/v1/atlas/meta").status_code == 200
    assert client.get("/api/v1/demo/matrix").json()["atlas_version"]
    # old root-level paths are gone
    assert client.get("/agents").status_code == 404


def test_health_not_shadowed_by_gateway():
    assert client.get("/api/v1/health").json() == {"status": "ok"}
