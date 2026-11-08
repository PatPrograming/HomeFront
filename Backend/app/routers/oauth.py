"""HomeFront Resource Authorization Server: ID-JAG shaped token exchange + jwt-bearer.

POST /api/v1/oauth/token (application/x-www-form-urlencoded)

1) Token exchange → ID-JAG assertion
   grant_type=urn:ietf:params:oauth:grant-type:token-exchange
   requested_token_type=urn:ietf:params:oauth:token-type:id-jag
   subject_token=<staff Cognito access|id token OR Homefront staff-subject JWT>
   subject_token_type=...
   resource / sid=<session id>
   Authorization: Bearer <agent Cognito client_credentials>

2) JWT bearer → aa-auth+jwt (PoP-bound mandate)
   grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
   assertion=<id-jag>
   client_jwk=<JSON public JWK of the agent>
   Authorization: Bearer <agent Cognito client_credentials>
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, status
from mandate_contracts import TTL_SECONDS

from aauth import (
    ID_JAG_TYP,
    auth_protocols_enabled,
    jwk_thumbprint,
    mint_aa_auth_jwt,
    mint_id_jag,
    verify_id_jag,
)
from identity_gateway.gateway import get_gateway
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import MandateClaims

try:
    from app.core.cognito import (
        AgentClientIdentity,
        StaffIdentity,
        require_agent_client,
        verify_staff_token,
    )
except ModuleNotFoundError as exc:
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import AgentClientIdentity, StaffIdentity, require_agent_client  # type: ignore

    def verify_staff_token(_token: str) -> StaffIdentity:  # type: ignore[misc]
        raise ValueError("Cognito verification is not installed")

router = APIRouter(tags=["oauth"])

TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ID_JAG_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id-jag"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"
AA_AUTH_TOKEN_TYPE = "urn:homefront:params:oauth:token-type:aa-auth"

SESSION_TTL_SECONDS = 60 * 60
AGENT_ID = os.environ.get("MANDATE_AGENT_ID", "agent-1")


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _created_epoch(created_at: str) -> float:
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except ValueError:
        return 0.0


def _staff_from_subject_token(subject_token: str) -> StaffIdentity:
    """Staff Cognito access token, or a Homefront test staff-subject JWT (HS256)."""
    try:
        return verify_staff_token(subject_token)
    except Exception:
        pass
    # Dev/test: HS256 staff-subject minted with the RAS/mandate secret.
    import jwt

    from aauth.tokens import ras_secret

    try:
        claims = jwt.decode(
            subject_token,
            ras_secret(),
            algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
        if claims.get("token_use") not in (None, "staff_subject", "access"):
            raise ValueError("not a staff subject")
        return StaffIdentity(
            username=claims["sub"],
            sub=claims.get("cognito_sub") or claims["sub"],
            email=claims.get("email"),
            groups=frozenset(claims.get("groups") or ["staffer"]),
        )
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid subject_token") from None


@router.post("/oauth/token")
def token(
    agent: AgentClientIdentity = Depends(require_agent_client),
    grant_type: str = Form(...),
    subject_token: str | None = Form(None),
    subject_token_type: str | None = Form(None),
    requested_token_type: str | None = Form(None),
    assertion: str | None = Form(None),
    resource: str | None = Form(None),
    sid: str | None = Form(None),
    client_jwk: str | None = Form(None),
    scope: str | None = Form(None),
) -> dict:
    protocols = auth_protocols_enabled()
    if not protocols.idjag and not protocols.aauth:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ID-JAG / AAuth token endpoint disabled")

    session_id = (sid or resource or "").strip()
    if grant_type == TOKEN_EXCHANGE:
        if not protocols.idjag:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "idjag protocol disabled")
        if requested_token_type not in (None, ID_JAG_TOKEN_TYPE):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "unsupported requested_token_type")
        if not subject_token or not session_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "subject_token and sid/resource are required")
        staff = _staff_from_subject_token(subject_token)
        gateway = get_gateway()
        session = gateway.store.get_session(session_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
        staffer = gateway.store.get_staffer(session.acting_for)
        if session.status != "active" or staffer is None or not staffer.active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Session revoked or staffer inactive")
        if session.acting_for != staff.username:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "subject_token is not the session staffer")
        now = int(time.time())
        if now > _created_epoch(session.created_at) + SESSION_TTL_SECONDS:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Session expired; start a new one")
        assertion_jwt = mint_id_jag(
            staffer=session.acting_for,
            agent_id=session.agent,
            office=session.office,
            sid=session_id,
            mode=session.mode,
            secret=gateway.secret,
        )
        return {
            "access_token": assertion_jwt,
            "issued_token_type": ID_JAG_TOKEN_TYPE,
            "token_type": "N_A",
            "expires_in": 300,
        }

    if grant_type == JWT_BEARER:
        if not protocols.aauth:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "aauth protocol disabled")
        if not assertion or not client_jwk:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "assertion and client_jwk are required")
        gateway = get_gateway()
        try:
            jag = verify_id_jag(assertion, secret=gateway.secret)
            jwk = json.loads(client_jwk)
            jkt = jwk_thumbprint(jwk)
        except Exception:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid assertion or client_jwk") from None
        session = gateway.store.get_session(jag["sid"])
        staffer = gateway.store.get_staffer(jag["sub"])
        if session is None or session.status != "active" or staffer is None or not staffer.active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Session revoked or staffer inactive")
        if session.acting_for != jag["sub"] or session.agent != jag["client_id"]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "assertion does not match session")
        now = int(time.time())
        auth_token = mint_aa_auth_jwt(
            agent_id=session.agent,
            staffer=session.acting_for,
            office=session.office,
            sid=jag["sid"],
            jkt=jkt,
            mode=session.mode,
            ttl_seconds=TTL_SECONDS,
            secret=gateway.secret,
            now=now,
        )
        # Dual-stack: also return a legacy mandate for clients that have not switched yet.
        legacy = None
        if protocols.legacy:
            claims = MandateClaims(
                sub=session.agent,
                act=session.acting_for,
                office=session.office,
                mode=session.mode,
                sid=jag["sid"],
                iat=now,
                exp=now + TTL_SECONDS,
            )
            legacy = mint_mandate_token(claims, gateway.secret)
        return {
            "access_token": auth_token,
            "issued_token_type": AA_AUTH_TOKEN_TYPE,
            "token_type": "Bearer",
            "expires_in": TTL_SECONDS,
            "expiresAt": _iso(now + TTL_SECONDS),
            "mandateToken": legacy,  # optional dual-stack convenience
            "cnf": {"jkt": jkt},
        }

    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported grant_type: {grant_type}")


@router.get("/.well-known/aauth-agent.json")
def aauth_agent_discovery() -> dict:
    """Gateway-published discovery shell; agents may also publish their own JWKS."""
    from aauth.tokens import ras_issuer

    return {
        "issuer": ras_issuer(),
        "token_endpoint": "/api/v1/oauth/token",
        "grant_types_supported": [TOKEN_EXCHANGE, JWT_BEARER],
        "id_jag_typ": ID_JAG_TYP,
        "protocols": auth_protocols_enabled().__dict__,
    }
