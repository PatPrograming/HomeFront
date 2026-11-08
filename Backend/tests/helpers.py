"""Test helpers: Cognito-shaped RS256 staff access tokens and the JWKS that verifies them
(served by the `cognito_login` fixture in conftest.py)."""
import json
import time
import uuid

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

REGION = "us-east-1"
USER_POOL_ID = "us-east-1_TestPool1"
APP_CLIENT_ID = "test-app-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
KID = "test-key-1"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
# A key the pool never published (forged-signature tests).
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

JWKS = {
    "keys": [
        {**json.loads(RSAAlgorithm.to_jwk(_KEY.public_key())), "kid": KID, "alg": "RS256", "use": "sig"}
    ]
}


def user_token(
    sub: str | None = None,
    *,
    groups: tuple[str, ...] = ("admin",),
    exp_in: int = 3600,
    key=None,
    **overrides,
) -> str:
    """A Cognito staff access token in the shape app.core.cognito expects (username,
    cognito:groups, client_id). `sub` is also the username, i.e. the agent owner id."""
    now = int(time.time())
    username = sub or str(uuid.uuid4())
    claims = {
        "sub": username,
        "username": username,
        "iss": ISSUER,
        "token_use": "access",
        "client_id": APP_CLIENT_ID,
        "scope": "openid email",
        "cognito:groups": list(groups),
        "iat": now,
        "exp": now + exp_in,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key or _KEY, algorithm="RS256", headers={"kid": KID})


def auth(sub: str | None = None, **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token(sub, **kwargs)}"}


# The default caller for the teammates' agent-API tests: a signed-in admin.
ADMIN_USER = "test-admin"
ADMIN = auth(ADMIN_USER)
