"""Supabase JWT verification as a FastAPI dependency."""
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None
    token: str  # raw JWT, forwarded to Supabase so RLS applies
    claims: dict


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    url = f"{get_settings().supabase_url}/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(url, cache_keys=True)


def _decode(token: str) -> dict:
    settings = get_settings()
    if settings.supabase_jwt_secret:
        return jwt.decode(
            token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated"
        )
    key = _jwks_client().get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = _decode(creds.credentials)
    except (jwt.PyJWTError, jwt.PyJWKClientError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return CurrentUser(
        id=claims["sub"], email=claims.get("email"), token=creds.credentials, claims=claims
    )
