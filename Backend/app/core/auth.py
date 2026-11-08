"""Amazon Cognito JWT verification as a FastAPI dependency.

Accepts either Cognito token type from the configured user pool and app client:
- ID token (what the frontend sends): `token_use: "id"`, `aud` = app client id.
  Carries `email` and `name`, used to create the profile on first sign-in.
- Access token: `token_use: "access"`, `client_id` = app client id.

Signature (RS256 via the pool's JWKS), issuer, expiry and client are all checked.
"""
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str  # Cognito `sub`
    email: str | None
    name: str | None
    token: str
    claims: dict


@lru_cache
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # Keyed by URL so a config change (or tests) gets a fresh client.
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


def _decode(token: str) -> dict:
    settings = get_settings()
    issuer = settings.cognito_issuer
    key = _jwks_client(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=issuer,
        options={"require": ["exp", "iat", "sub", "token_use"], "verify_aud": False},
    )
    token_use = claims.get("token_use")
    client_id = settings.cognito_app_client_id
    if token_use == "id":
        if claims.get("aud") != client_id:
            raise jwt.InvalidAudienceError("ID token was issued for a different app client")
    elif token_use == "access":
        if claims.get("client_id") != client_id:
            raise jwt.InvalidAudienceError("Access token was issued for a different app client")
    else:
        raise jwt.InvalidTokenError(f"Unsupported token_use: {token_use!r}")
    return claims


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    if not get_settings().cognito_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "User auth is not configured (set COGNITO_USER_POOL_ID and COGNITO_APP_CLIENT_ID)",
        )
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = _decode(creds.credentials)
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return CurrentUser(
        id=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name"),
        token=creds.credentials,
        claims=claims,
    )
