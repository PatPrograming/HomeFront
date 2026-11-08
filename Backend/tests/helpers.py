"""Test helpers: Cognito-shaped RS256 tokens and the JWKS that verifies them."""
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
    token_use: str = "id",
    email: str | None = "user@example.gov",
    name: str | None = "Test User",
    exp_in: int = 300,
    key=None,
    **overrides,
) -> str:
    now = int(time.time())
    claims = {
        "sub": sub or str(uuid.uuid4()),
        "iss": ISSUER,
        "token_use": token_use,
        "iat": now,
        "exp": now + exp_in,
        "auth_time": now,
    }
    if token_use == "id":
        claims.update(aud=APP_CLIENT_ID, email=email, name=name, email_verified=True)
    else:
        claims.update(client_id=APP_CLIENT_ID, scope="aws.cognito.signin.user.admin")
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key or _KEY, algorithm="RS256", headers={"kid": KID})


def auth(sub: str | None = None, **kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_token(sub, **kwargs)}"}
