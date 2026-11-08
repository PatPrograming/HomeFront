"""User login for routes, backed by Amazon Cognito (see app/core/cognito.py).

Kept as a thin wrapper so older imports (`CurrentUser`, `bearer`, `get_current_user`)
keep working. New routes should use `require_staff` / `require_admin` directly.
"""
from dataclasses import asdict, dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.cognito import INVALID_TOKEN, bearer, verify_staff_token

__all__ = ["CurrentUser", "bearer", "get_current_user"]


@dataclass(frozen=True)
class CurrentUser:
    id: str  # Cognito username, e.g. "maya" (the staffer id used across HomeFront)
    email: str | None
    token: str  # raw access token
    claims: dict


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    """Any valid staff-web access token; no group check (use require_staff for that)."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_TOKEN)
    identity = verify_staff_token(creds.credentials)
    return CurrentUser(
        id=identity.username, email=identity.email, token=creds.credentials, claims=asdict(identity)
    )
