import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from tests.helpers import OTHER_KEY, auth, user_token

client = TestClient(app)


def _me(headers):
    return client.get("/api/v1/me", headers=headers)


def test_health():
    assert client.get("/api/v1/health").json() == {"status": "ok"}


# --- token verification ---------------------------------------------------


def test_me_requires_token():
    assert client.get("/api/v1/me").status_code == 401


def test_id_token_accepted():
    assert _me(auth()).status_code == 200


def test_access_token_accepted():
    assert _me(auth(token_use="access")).status_code == 200


@pytest.mark.parametrize(
    "kwargs",
    [
        {"exp_in": -60},
        {"key": OTHER_KEY},
        {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_OtherPool"},
        {"aud": "some-other-client"},
        {"token_use": "access", "client_id": "some-other-client"},
        {"token_use": "refresh"},
    ],
    ids=["expired", "forged-signature", "wrong-pool", "id-wrong-client", "access-wrong-client", "bad-token-use"],
)
def test_bad_tokens_rejected(kwargs):
    assert _me(auth(**kwargs)).status_code == 401


def test_missing_required_claim_rejected():
    assert _me(auth(token_use=None)).status_code == 401


def test_garbage_token_rejected():
    assert _me({"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_unconfigured_pool_is_503():
    settings = get_settings()
    saved = settings.cognito_user_pool_id
    settings.cognito_user_pool_id = ""
    try:
        assert _me(auth()).status_code == 503
    finally:
        settings.cognito_user_pool_id = saved


# --- profiles (DynamoDB) --------------------------------------------------


def test_first_request_creates_profile_from_token():
    sub = str(uuid.uuid4())
    body = _me(auth(sub, name="Ada Lovelace")).json()
    assert body["id"] == sub
    assert body["full_name"] == "Ada Lovelace"
    assert body["username"] is None
    # Second call returns the same row, not a fresh one.
    again = _me(auth(sub, name="Changed In Cognito")).json()
    assert again == body


def test_patch_updates_own_profile_only():
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    _me(auth(bob))
    r = client.patch("/api/v1/me", json={"full_name": "Alice A."}, headers=auth(alice))
    assert r.status_code == 200
    assert r.json()["full_name"] == "Alice A."
    assert r.json()["updated_at"] >= r.json()["created_at"]
    assert _me(auth(bob)).json()["full_name"] == "Test User"


def test_patch_rejects_unknown_fields():
    r = client.patch("/api/v1/me", json={"id": "someone-else"}, headers=auth())
    assert r.status_code == 422


def test_username_is_unique():
    name = f"user{uuid.uuid4().hex[:8]}"
    a, b = auth(), auth()
    assert client.patch("/api/v1/me", json={"username": name}, headers=a).status_code == 200
    r = client.patch("/api/v1/me", json={"username": name}, headers=b)
    assert r.status_code == 409


def test_username_change_releases_old_name():
    old, new = f"old{uuid.uuid4().hex[:8]}", f"new{uuid.uuid4().hex[:8]}"
    a = str(uuid.uuid4())
    client.patch("/api/v1/me", json={"username": old}, headers=auth(a))
    r = client.patch("/api/v1/me", json={"username": new}, headers=auth(a))
    assert r.status_code == 200 and r.json()["username"] == new
    # Someone else can now take the old one.
    assert client.patch("/api/v1/me", json={"username": old}, headers=auth()).status_code == 200


def test_username_can_be_cleared_and_reused():
    name = f"clr{uuid.uuid4().hex[:8]}"
    a = str(uuid.uuid4())
    client.patch("/api/v1/me", json={"username": name}, headers=auth(a))
    r = client.patch("/api/v1/me", json={"username": None}, headers=auth(a))
    assert r.status_code == 200 and r.json()["username"] is None
    assert client.patch("/api/v1/me", json={"username": name}, headers=auth()).status_code == 200


def test_setting_same_username_again_is_fine():
    name = f"same{uuid.uuid4().hex[:8]}"
    a = str(uuid.uuid4())
    client.patch("/api/v1/me", json={"username": name}, headers=auth(a))
    r = client.patch("/api/v1/me", json={"username": name, "full_name": "X"}, headers=auth(a))
    assert r.status_code == 200 and r.json()["full_name"] == "X"


@pytest.mark.parametrize("username", ["ab", "x" * 33])
def test_username_length_validated(username):
    assert client.patch("/api/v1/me", json={"username": username}, headers=auth()).status_code == 422


def test_patch_before_first_get_creates_profile():
    sub = str(uuid.uuid4())
    r = client.patch("/api/v1/me", json={"avatar_url": "https://x/y.png"}, headers=auth(sub))
    assert r.status_code == 200
    assert r.json()["avatar_url"] == "https://x/y.png"
    assert r.json()["full_name"] == "Test User"


def test_token_helper_round_trip():
    # Sanity check on the test helper itself.
    import jwt

    claims = jwt.decode(user_token("abc"), options={"verify_signature": False})
    assert claims["sub"] == "abc" and claims["token_use"] == "id"
