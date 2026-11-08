"""Amazon Cognito token verification (the lane A -> B interface in docs/LANES.md).

Two kinds of caller reach the backend, each with its own Cognito app client:
  * staff (Maya, ChiefOfStaff) log in through `staff-web` and send an access token;
  * the agent uses `mandate-agent` (client_credentials) and sends an access token with
    the `mandate/tools` scope.

Every check fails closed: any problem with a token is a 401 that never says why, and a
valid staffer in the wrong group is a 403. Cognito tokens are verified locally until
`exp`, so they cannot be revoked here; revocation is the DynamoDB check in lane B.
"""

import ssl
from dataclasses import dataclass
from functools import lru_cache

import certifi  # installed with httpx; a maintained Mozilla CA bundle
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mandate_contracts import SCOPE

from app.core.config import get_settings

bearer = HTTPBearer(auto_error=False)

STAFF_GROUPS = frozenset({"staffer", "admin"})
INVALID_TOKEN = "Authentication failed"  # one message for every failure: never reveal why


@dataclass(frozen=True)
class StaffIdentity:
    username: str
    sub: str
    email: str | None
    groups: frozenset[str]


@dataclass(frozen=True)
class AgentClientIdentity:
    client_id: str
    scopes: frozenset[str]


def _issuer() -> str:
    settings = get_settings()
    return f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/{settings.cognito_user_pool_id}"


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    # Bring our own CA bundle: python.org Python on macOS has none, so without this the
    # HTTPS fetch of the pool's keys fails and every real token is (safely) rejected.
    context = ssl.create_default_context(cafile=certifi.where())
    return jwt.PyJWKClient(url, cache_keys=True, ssl_context=context)


def _signing_key(token: str):
    """The pool's public key for this token's `kid` (tests patch `jwt.PyJWKClient.fetch_data`)."""
    return _jwks_client(f"{_issuer()}/.well-known/jwks.json").get_signing_key_from_jwt(token).key


def _decode_access_token(token: str, client_id: str) -> dict:
    # An unconfigured pool or client id must reject everything, before any network call.
    if not get_settings().cognito_user_pool_id or not client_id:
        raise ValueError("Cognito is not configured")
    claims = jwt.decode(
        token,
        _signing_key(token),
        algorithms=["RS256"],  # fixed list: blocks HS256 / "none" algorithm-confusion tricks
        issuer=_issuer(),  # only tokens minted by our pool
        # Access tokens have no `aud`; the client is checked through `client_id` below.
        options={"require": ["exp", "iss", "token_use", "client_id"], "verify_aud": False},
    )
    # ID tokens are signed by the same pool but are not meant for APIs.
    if claims["token_use"] != "access":
        raise ValueError("Not an access token")
    # Stops a token from the other app client (e.g. an agent token used as a staff login).
    if claims["client_id"] != client_id:
        raise ValueError("Token was issued to a different client")
    return claims


def verify_staff_token(token: str) -> StaffIdentity:
    try:
        claims = _decode_access_token(token, get_settings().cognito_staff_client_id)
        return StaffIdentity(
            username=claims["username"],
            sub=claims["sub"],
            email=claims.get("email"),  # not in Cognito access tokens by default
            groups=frozenset(claims.get("cognito:groups", [])),  # absent when in no group
        )
    except Exception:  # deliberately broad: whatever goes wrong, the answer is 401
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_TOKEN) from None


def verify_agent_client_token(token: str) -> AgentClientIdentity:
    try:
        claims = _decode_access_token(token, get_settings().cognito_agent_client_id)
        scopes = frozenset(claims.get("scope", "").split())  # space-separated string
        if SCOPE not in scopes:
            raise ValueError("Missing mandate/tools scope")
        return AgentClientIdentity(client_id=claims["client_id"], scopes=scopes)
    except Exception:  # deliberately broad: whatever goes wrong, the answer is 401
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_TOKEN) from None


def require_staff(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> StaffIdentity:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_TOKEN)
    identity = verify_staff_token(creds.credentials)
    # Outside the try in verify_*, so a wrong group stays a 403 and is never turned into a 401.
    if not identity.groups & STAFF_GROUPS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a staff member")
    return identity


def require_admin(identity: StaffIdentity = Depends(require_staff)) -> StaffIdentity:
    if "admin" not in identity.groups:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return identity


def require_agent_client(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AgentClientIdentity:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_TOKEN)
    return verify_agent_client_token(creds.credentials)
