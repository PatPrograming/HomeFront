import time

import jwt
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SECRET = "test-secret-test-secret-test-secret"


def _token(**overrides) -> str:
    claims = {"sub": "00000000-0000-0000-0000-000000000001", "aud": "authenticated",
              "exp": int(time.time()) + 60, **overrides}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_health():
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_me_requires_token():
    assert client.get("/api/v1/me").status_code == 401


def test_me_rejects_expired_token():
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {_token(exp=1)}"})
    assert r.status_code == 401


def test_me_rejects_wrong_audience():
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {_token(aud='anon')}"})
    assert r.status_code == 401
