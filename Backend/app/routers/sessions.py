"""Broker (WP2): a staffer starts a session, then the agent picks up its mandate token.
The console reads a session's status back to show when it was revoked.

Two different credentials are needed before any mandate token exists:
1. `POST /sessions` needs the staffer's Cognito login.
2. `POST /sessions/{sid}/mandate` needs the agent's own Cognito client credentials.
"""

import os
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from identity_gateway.gateway import get_gateway
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import TOOLS, TTL_SECONDS, MandateClaims
from mandate_contracts.ddb import SessionItem

try:
    from app.core.cognito import AgentClientIdentity, StaffIdentity, require_agent_client, require_staff
except ModuleNotFoundError as exc:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import AgentClientIdentity, StaffIdentity, require_agent_client, require_staff

router = APIRouter(tags=["broker"])

AGENT_ID = os.environ.get("MANDATE_AGENT_ID", "agent-1")
SESSION_TTL_SECONDS = 60 * 60  # a session can mint 15-minute mandate tokens for one hour
ALLOWED_TOOLS = [name for name, spec in TOOLS.items() if not spec.always_denied]


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _created_epoch(created_at: str) -> float:
    """Session start as epoch seconds; an unreadable timestamp counts as long expired."""
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except ValueError:
        return 0.0


@router.post("/sessions")
def create_session(staff: StaffIdentity = Depends(require_staff)) -> dict:
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    if staffer is None or not staffer.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staffer is not active")
    started = time.time()
    sid = str(uuid.uuid4())
    session = SessionItem(
        status="active",
        agent=AGENT_ID,
        acting_for=staff.username,
        office=staffer.office,  # office comes from the STAFFER item, not from Cognito or the client
        mode="official",
        created_at=datetime.fromtimestamp(started, timezone.utc).isoformat(),  # sub-second, so tabs sort newest first
    )
    store.put_session(sid, session)
    return {
        "sid": sid,
        "agent": session.agent,
        "actingFor": session.acting_for,
        "office": session.office,
        "mode": session.mode,
        "allowedTools": ALLOWED_TOOLS,
        "expiresAt": _iso(started + SESSION_TTL_SECONDS),
    }


TITLE_CHARS = 60


def _title(sid: str) -> str:
    """The conversation's first message, on one line and cut to fit a tab."""
    first = get_gateway().store.first_chat_message(sid)
    text = " ".join(first.text.split()) if first else ""
    if not text:
        return "New conversation"
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1].rstrip() + "…"


def _status(session: SessionItem, staffer_active: bool, now: float) -> str:
    if session.status != "active" or not staffer_active:
        return "revoked"
    return "expired" if now >= _created_epoch(session.created_at) + SESSION_TTL_SECONDS else "active"


@router.get("/sessions")
def list_sessions(staff: StaffIdentity = Depends(require_staff)) -> dict:
    """The caller's own conversations, newest first. Nobody can list another staffer's."""
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    active = staffer is not None and staffer.active
    waiting = Counter(draft.sid for _, draft in store.pending_drafts_for(staff.username))
    now = time.time()
    mine = sorted(store.sessions_for(staff.username), key=lambda pair: pair[1].created_at, reverse=True)
    return {
        "sessions": [
            {
                "sid": sid,
                "title": _title(sid),
                "createdAt": session.created_at,
                "expiresAt": _iso(_created_epoch(session.created_at) + SESSION_TTL_SECONDS),
                "status": _status(session, active, now),
                "waitingDrafts": waiting[sid],
            }
            for sid, session in mine
        ]
    }


@router.get("/sessions/{sid}")
def read_session(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """The session's own staffer, or an admin, sees its status. Anyone else gets 404, as if it did not exist."""
    store = get_gateway().store
    session = store.get_session(sid)
    if session is None or (session.acting_for != staff.username and "admin" not in staff.groups):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    staffer = store.get_staffer(session.acting_for)
    # Same rule as the gateway: an inactive or missing staffer means the session is over.
    active = session.status == "active" and staffer is not None and staffer.active
    return {
        "sid": sid,
        "status": "active" if active else "revoked",
        "actingFor": session.acting_for,
        "office": session.office,
        "expiresAt": _iso(_created_epoch(session.created_at) + SESSION_TTL_SECONDS),
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
    if now > _created_epoch(session.created_at) + SESSION_TTL_SECONDS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Session expired; start a new one")
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
