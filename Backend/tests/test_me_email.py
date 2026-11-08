"""GET /api/v1/me fills `email` from Cognito's userInfo endpoint when the access token has none.

Contract for the implementation: in `app.routers.profiles`, `import httpx` and call exactly

    httpx.get(f"https://{settings.cognito_domain}/oauth2/userInfo",
              headers={"Authorization": f"Bearer {token}"}, timeout=...)

The tests patch `app.routers.profiles.httpx.get`, so no request leaves the process. The
result is cached per `sub`; any failure means `email: null` with a 200.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.helpers import user_token


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=httpx.Request("GET", "https://x"), response=httpx.Response(self.status_code))


@pytest.fixture
def calls(monkeypatch):
    """Patched httpx.get in app.routers.profiles; `calls.response` is what it returns (or raises)."""
    from app.core.config import get_settings

    monkeypatch.setenv("COGNITO_DOMAIN", "homefront-test.auth.us-east-1.amazoncognito.com")
    get_settings.cache_clear()

    class Calls(list):
        response: object = FakeResponse(200, {"sub": "x", "email": "maya@example.gov"})

    spy = Calls()

    def fake_get(url, *args, **kwargs):
        spy.append((url, kwargs))
        if isinstance(spy.response, Exception):
            raise spy.response
        return spy.response

    monkeypatch.setattr("app.routers.profiles.httpx.get", fake_get)
    yield spy
    get_settings.cache_clear()


@pytest.fixture
def client(cognito_login, calls):
    return TestClient(app)


def fresh_sub() -> str:
    return f"user-{uuid.uuid4()}"  # the per-sub cache must not leak between tests


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_me_fetches_email_from_userinfo_when_the_token_has_none(client, calls):
    token = user_token(fresh_sub(), groups=("staffer",))
    r = client.get("/api/v1/me", headers=bearer(token))
    assert r.status_code == 200
    assert r.json()["email"] == "maya@example.gov"
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://homefront-test.auth.us-east-1.amazoncognito.com/oauth2/userInfo"
    assert kwargs["headers"] == {"Authorization": f"Bearer {token}"}
    assert kwargs.get("timeout")  # never an unbounded wait


def test_me_email_is_null_and_200_when_userinfo_fails(client, calls):
    calls.response = httpx.ConnectError("down")
    r = client.get("/api/v1/me", headers=bearer(user_token(fresh_sub(), groups=("staffer",))))
    assert r.status_code == 200
    assert r.json()["email"] is None


def test_me_email_is_null_when_userinfo_returns_an_error_status(client, calls):
    calls.response = FakeResponse(401, {"error": "invalid_token"})
    r = client.get("/api/v1/me", headers=bearer(user_token(fresh_sub(), groups=("staffer",))))
    assert r.status_code == 200
    assert r.json()["email"] is None


def test_me_does_not_call_userinfo_when_the_token_already_has_an_email(client, calls):
    token = user_token(fresh_sub(), groups=("staffer",), email="in-token@example.gov")
    r = client.get("/api/v1/me", headers=bearer(token))
    assert r.status_code == 200
    assert r.json()["email"] == "in-token@example.gov"
    assert calls == []


def test_me_caches_the_email_per_sub(client, calls):
    sub = fresh_sub()
    first = client.get("/api/v1/me", headers=bearer(user_token(sub, groups=("staffer",))))
    second = client.get("/api/v1/me", headers=bearer(user_token(sub, groups=("staffer",))))
    assert first.json()["email"] == second.json()["email"] == "maya@example.gov"
    assert len(calls) == 1
