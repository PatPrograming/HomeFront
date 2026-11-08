"""Cognito token verification (lane A -> B interface in docs/LANES.md).

Offline only: tokens are signed with a locally generated RSA key (with a `kid`) and
`jwt.PyJWKClient.fetch_data` is patched to serve the matching public JWK, so the real
key lookup, kid matching and JWKS URL construction all run. No AWS calls.
"""

import base64
import json
import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from mandate_contracts import SCOPE

from app.core.config import get_settings

REGION = "us-east-1"
POOL_ID = "us-east-1_TEST"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"
STAFF_CLIENT = "staff-client"
AGENT_CLIENT = "agent-client"
KID = "test-kid"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUBLIC_KEY = _PRIVATE_KEY.public_key()
PUBLIC_JWK = {
    **jwt.algorithms.RSAAlgorithm.to_jwk(PUBLIC_KEY, as_dict=True),
    "kid": KID, "alg": "RS256", "use": "sig",
}


def make_token(key=None, headers=None, algorithm="RS256", **claims) -> str:
    """A valid Cognito staff access token for maya; pass claim=None to drop a claim."""
    now = int(time.time())
    body = {
        "sub": "sub-maya-123",
        "iss": ISSUER,
        "client_id": STAFF_CLIENT,
        "token_use": "access",
        "scope": "openid email profile",
        "username": "maya",
        "cognito:groups": ["staffer"],
        "exp": now + 300,
        "iat": now,
        "jti": str(uuid.uuid4()),
    }
    for name, value in claims.items():
        if value is None:
            body.pop(name, None)
        else:
            body[name] = value
    return jwt.encode(body, key or _PRIVATE_KEY, algorithm=algorithm,
                      headers={"kid": KID, **(headers or {})})


def agent_token(**claims) -> str:
    """A client_credentials access token: no username, no groups, scope mandate/tools."""
    defaults = {"sub": AGENT_CLIENT, "client_id": AGENT_CLIENT, "scope": SCOPE,
                "username": None, "cognito:groups": None}
    return make_token(**{**defaults, **claims})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def jwks(monkeypatch):
    """Serve the test public JWK instead of calling AWS; records every fetch's URL."""
    from app.core import cognito

    fetched: list[str] = []

    def fake_fetch_data(self):
        fetched.append(self.uri)
        return {"keys": [PUBLIC_JWK]}

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch_data)
    cognito._jwks_client.cache_clear()
    yield fetched
    cognito._jwks_client.cache_clear()


@pytest.fixture(autouse=True)
def cognito_env(monkeypatch, jwks):

    monkeypatch.setenv("COGNITO_USER_POOL_ID", POOL_ID)
    monkeypatch.setenv("COGNITO_STAFF_CLIENT_ID", STAFF_CLIENT)
    monkeypatch.setenv("COGNITO_AGENT_CLIENT_ID", AGENT_CLIENT)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def cognito_mod():
    from app.core import cognito

    return cognito


@pytest.fixture
def guarded_client(cognito_mod):
    """Tiny app with one route per require_* dependency."""
    app = FastAPI()

    @app.get("/staff")
    def staff(identity=Depends(cognito_mod.require_staff)):
        return {"username": identity.username, "groups": sorted(identity.groups)}

    @app.get("/admin")
    def admin(identity=Depends(cognito_mod.require_admin)):
        return {"username": identity.username}

    @app.get("/agent")
    def agent(identity=Depends(cognito_mod.require_agent_client)):
        return {"client_id": identity.client_id, "scopes": sorted(identity.scopes)}

    return TestClient(app)


@pytest.fixture
def me_client():
    from app.main import app

    return TestClient(app)


def assert_401(fn, token: str) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        fn(token)
    assert exc.value.status_code == 401
    return exc.value


# --- verify_staff_token / verify_agent_client_token (T3) -------------------------------


def test_verify_staff_accepts_valid_token(cognito_mod):
    identity = cognito_mod.verify_staff_token(make_token())
    assert identity.username == "maya"
    assert identity.sub == "sub-maya-123"
    assert identity.groups == frozenset({"staffer"})
    assert identity.email is None


def test_verify_staff_rejects_tampered_token(cognito_mod):
    # Payload edited after signing (maya promoted to admin), signature kept.
    header, payload, signature = make_token().split(".")
    padded = payload + "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    claims["cognito:groups"] = ["admin"]
    forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    assert_401(cognito_mod.verify_staff_token, f"{header}.{forged}.{signature}")
    # Well-formed token signed by a different private key.
    assert_401(cognito_mod.verify_staff_token, make_token(key=_OTHER_KEY))


def test_verify_staff_rejects_expired_token(cognito_mod):
    assert_401(cognito_mod.verify_staff_token, make_token(exp=int(time.time()) - 600))


def test_verify_staff_rejects_wrong_issuer(cognito_mod):
    other = f"https://cognito-idp.{REGION}.amazonaws.com/us-east-1_OTHER"
    assert_401(cognito_mod.verify_staff_token, make_token(iss=other))


def test_verify_staff_rejects_wrong_client(cognito_mod):
    assert_401(cognito_mod.verify_staff_token, make_token(client_id=AGENT_CLIENT))


def test_verify_staff_rejects_id_token(cognito_mod):
    # Everything valid (including client_id) except token_use: only that check can reject it.
    assert_401(cognito_mod.verify_staff_token, make_token(token_use="id"))


def test_verify_staff_rejects_real_id_token_shape(cognito_mod):
    id_token = make_token(token_use="id", aud=STAFF_CLIENT, client_id=None)
    assert_401(cognito_mod.verify_staff_token, id_token)


def test_verify_staff_rejects_token_without_exp(cognito_mod):
    assert_401(cognito_mod.verify_staff_token, make_token(exp=None))


def test_verify_staff_rejects_token_without_token_use(cognito_mod):
    assert_401(cognito_mod.verify_staff_token, make_token(token_use=None))


def test_verify_staff_rejects_kid_not_in_jwks(cognito_mod, guarded_client):
    unknown = make_token(headers={"kid": "unknown-kid"})
    assert_401(cognito_mod.verify_staff_token, unknown)
    assert guarded_client.get("/staff", headers=bearer(unknown)).status_code == 401


def test_jwks_url_is_the_pool_well_known_endpoint(cognito_mod, jwks):
    cognito_mod.verify_staff_token(make_token())
    assert jwks == [JWKS_URL]
    assert JWKS_URL == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TEST/.well-known/jwks.json"


def test_verify_staff_rejects_hs256_token(cognito_mod):
    hs_token = make_token(key="x" * 48, algorithm="HS256")
    assert_401(cognito_mod.verify_staff_token, hs_token)


def test_verify_rejects_everything_when_pool_unconfigured(cognito_mod, monkeypatch, jwks):
    # Empty env vars override any .env file; the settings cache must be rebuilt.
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "")
    monkeypatch.setenv("COGNITO_STAFF_CLIENT_ID", "")
    monkeypatch.setenv("COGNITO_AGENT_CLIENT_ID", "")
    get_settings.cache_clear()
    assert_401(cognito_mod.verify_staff_token, make_token())
    assert_401(cognito_mod.verify_agent_client_token, agent_token())
    assert jwks == []  # the JWKS was never fetched: no network call when unconfigured


def test_verify_401_detail_is_identical_for_every_failure(cognito_mod):
    failures = [
        make_token(key=_OTHER_KEY),
        make_token(exp=int(time.time()) - 600),
        make_token(iss="https://evil.example/pool"),
        make_token(client_id=AGENT_CLIENT),
        make_token(token_use="id", aud=STAFF_CLIENT, client_id=None),
        "not-a-jwt",
    ]
    details = {assert_401(cognito_mod.verify_staff_token, t).detail for t in failures}
    details.add(assert_401(cognito_mod.verify_agent_client_token,
                           agent_token(scope="openid")).detail)
    assert len(details) == 1
    detail = details.pop().lower()
    for reason in ("expire", "signature", "issuer", "client", "scope", "token_use"):
        assert reason not in detail


def test_verify_agent_accepts_client_credentials_token(cognito_mod):
    identity = cognito_mod.verify_agent_client_token(agent_token())
    assert identity.client_id == AGENT_CLIENT
    assert identity.scopes == frozenset({SCOPE})


def test_verify_agent_rejects_missing_scope(cognito_mod):
    assert_401(cognito_mod.verify_agent_client_token, agent_token(scope="openid email"))
    assert_401(cognito_mod.verify_agent_client_token, agent_token(scope=None))
    # A scope that merely contains the text is not the scope.
    assert_401(cognito_mod.verify_agent_client_token, agent_token(scope=f"{SCOPE}-extra"))


def test_verify_agent_rejects_staff_client(cognito_mod):
    assert_401(cognito_mod.verify_agent_client_token, agent_token(client_id=STAFF_CLIENT, scope=SCOPE))
    assert_401(cognito_mod.verify_agent_client_token, make_token(scope=SCOPE))


# --- require_staff / require_admin / require_agent_client (T4) -------------------------


def test_require_staff_no_token_401(guarded_client):
    assert guarded_client.get("/staff").status_code == 401


def test_require_staff_no_group_403(guarded_client):
    for groups in (None, [], ["somebody-else"]):
        r = guarded_client.get("/staff", headers=bearer(make_token(**{"cognito:groups": groups})))
        assert r.status_code == 403, groups


def test_require_staff_accepts_staffer_and_admin(guarded_client):
    assert guarded_client.get("/staff", headers=bearer(make_token())).status_code == 200
    admin = make_token(username="chiefofstaff", **{"cognito:groups": ["admin"]})
    assert guarded_client.get("/staff", headers=bearer(admin)).status_code == 200


def test_require_staff_bad_token_is_401_not_403(guarded_client):
    r = guarded_client.get("/staff", headers=bearer(make_token(key=_OTHER_KEY)))
    assert r.status_code == 401


def test_require_admin_403_for_maya(guarded_client):
    assert guarded_client.get("/admin", headers=bearer(make_token())).status_code == 403


def test_require_admin_allows_chiefofstaff(guarded_client):
    token = make_token(username="chiefofstaff", sub="sub-chief-1", **{"cognito:groups": ["admin"]})
    r = guarded_client.get("/admin", headers=bearer(token))
    assert r.status_code == 200
    assert r.json() == {"username": "chiefofstaff"}


def test_require_admin_no_token_401(guarded_client):
    assert guarded_client.get("/admin").status_code == 401


def test_require_agent_client_no_token_401(guarded_client):
    assert guarded_client.get("/agent").status_code == 401


def test_require_agent_client_accepts_agent_token(guarded_client):
    r = guarded_client.get("/agent", headers=bearer(agent_token()))
    assert r.status_code == 200
    assert r.json() == {"client_id": AGENT_CLIENT, "scopes": [SCOPE]}


def test_require_agent_client_rejects_staff_token(guarded_client):
    assert guarded_client.get("/agent", headers=bearer(make_token())).status_code == 401


# --- app.core.auth.get_current_user (T5) -----------------------------------------------


def test_current_user_from_cognito_staff_token():
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core import auth

    token = make_token()
    user = auth.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert user.id == "maya"
    assert user.token == token
    assert user.email is None
    assert user.claims["sub"] == "sub-maya-123"


def test_current_user_rejects_supabase_hs256_token():
    from fastapi.security import HTTPAuthorizationCredentials

    from app.core import auth

    secret = "test-secret-test-secret-test-secret"
    supabase = jwt.encode(
        {"sub": "00000000-0000-0000-0000-000000000001", "aud": "authenticated",
         "exp": int(time.time()) + 60},
        secret, algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc:
        auth.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=supabase))
    assert exc.value.status_code == 401


# --- GET /api/v1/me (T7, C3) -----------------------------------------------------------


def test_me_returns_cognito_identity(me_client):
    r = me_client.get("/api/v1/me", headers=bearer(make_token()))
    assert r.status_code == 200
    assert r.json() == {"username": "maya", "sub": "sub-maya-123", "email": None,
                        "groups": ["staffer"]}


def test_me_rejects_agent_token(me_client):
    assert me_client.get("/api/v1/me", headers=bearer(agent_token())).status_code == 401


def test_me_requires_token(me_client):
    # A bad token must not slip through either; the no-token case needs Cognito wiring to be meaningful.
    assert me_client.get("/api/v1/me").status_code == 401
    assert me_client.get("/api/v1/me", headers=bearer(make_token(key=_OTHER_KEY))).status_code == 401


def test_jwks_client_trusts_the_certifi_ca_bundle():
    # python.org Python on macOS ships without CA certificates, so a default HTTPS fetch of
    # the JWKS fails and every real token is rejected. The client must bring its own CAs.
    import ssl

    import certifi

    from app.core import cognito

    context = cognito._jwks_client("https://example.invalid/jwks.json").ssl_context
    assert isinstance(context, ssl.SSLContext)
    expected = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert context.cert_store_stats()["x509_ca"] == expected > 0
