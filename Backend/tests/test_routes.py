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


def test_demo_matrix_includes_hover_descriptions():
    body = client.get("/api/v1/demo/matrix").json()
    by_id = {t["id"]: t for t in body["techniques"]}
    by_id.update({m["id"]: m for m in body["mitigations"]})
    assert "AML.T0053" in by_id and by_id["AML.T0053"]["name"]
    assert "AML.M0028" in by_id and "access" in (by_id["AML.M0028"]["description"] or "").lower()
    # Tool-linked IDs (not only the Prompt 1 staples) are included for Activity/Monitoring hover.
    assert "AML.T0099" in by_id
    assert set(by_id["AML.T0053"]) <= {"id", "name", "description"}


def test_health_not_shadowed_by_gateway():
    assert client.get("/api/v1/health").json() == {"status": "ok"}
