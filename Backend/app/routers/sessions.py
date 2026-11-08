"""Broker (WP2): a staffer starts a session, then the agent picks up its mandate token.

Two different credentials are needed before any mandate token exists:
1. `POST /sessions` needs the staffer's Cognito login.
2. `POST /sessions/{sid}/mandate` needs the agent's own Cognito client credentials.
"""

import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from identity_gateway.gateway import get_gateway
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import TOOLS, TTL_SECONDS, MandateClaims
from mandate_contracts.ddb import SessionItem

try:
    from app.core.cognito import AgentClientIdentity, StaffIdentity, require_agent_client, require_staff
except ImportError:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    from identity_gateway.auth_stub import AgentClientIdentity, StaffIdentity, require_agent_client, require_staff

router = APIRouter(tags=["broker"])

AGENT_ID = os.environ.get("MANDATE_AGENT_ID", "agent-1")
ALLOWED_TOOLS = [name for name, spec in TOOLS.items() if not spec.always_denied]


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


@router.post("/sessions")
def create_session(staff: StaffIdentity = Depends(require_staff)) -> dict:
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    if staffer is None or not staffer.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staffer is not active")
    now = int(time.time())
    sid = str(uuid.uuid4())
    session = SessionItem(
        status="active",
        agent=AGENT_ID,
        acting_for=staff.username,
        office=staffer.office,  # office comes from the STAFFER item, not from Cognito or the client
        mode="official",
        created_at=_iso(now),
    )
    store.put_session(sid, session)
    return {
        "sid": sid,
        "agent": session.agent,
        "actingFor": session.acting_for,
        "office": session.office,
        "mode": session.mode,
        "allowedTools": ALLOWED_TOOLS,
        "expiresAt": _iso(now + TTL_SECONDS),
    }


@router.post("/sessions/{sid}/mandate")
def issue_mandate(sid: str, agent: AgentClientIdentity = Depends(require_agent_client)) -> dict:
    gateway = get_gateway()
    session = gateway.store.get_session(sid)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    staffer = gateway.store.get_staffer(session.acting_for)
    if session.status != "active" or staffer is None or not staffer.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Session revoked or staffer inactive")
    now = int(time.time())
    claims = MandateClaims(
        sub=session.agent,
        act=session.acting_for,
        office=session.office,
        mode=session.mode,
        sid=sid,
        iat=now,
        exp=now + TTL_SECONDS,
    )
    return {"mandateToken": mint_mandate_token(claims, gateway.secret), "expiresAt": _iso(claims.exp)}
