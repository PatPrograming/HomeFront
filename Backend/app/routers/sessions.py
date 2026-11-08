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

from fastapi import APIRouter, Depends, HTTPException, Query, status

from identity_gateway.gateway import get_gateway
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import TOOLS, TTL_SECONDS, MandateClaims
from mandate_contracts.ddb import SessionItem
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

try:
    from app.core.cognito import AgentClientIdentity, StaffIdentity, require_agent_client, require_staff
except ModuleNotFoundError as exc:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import AgentClientIdentity, StaffIdentity, require_agent_client, require_staff

from app.services import conversations  # noqa: E402
from app.services.agent_supervisor import get_supervisor  # noqa: E402

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


def _status(session: SessionItem, staffer_active: bool, now: float) -> str:
    if session.status != "active" or not staffer_active:
        return "revoked"
    return "expired" if now >= _created_epoch(session.created_at) + SESSION_TTL_SECONDS else "active"


def _row(sid: str, session: SessionItem, meta: conversations.ConversationMeta, staffer_active: bool, waiting: int) -> dict:
    last = get_gateway().store.last_chat_message(sid)
    return {
        "sid": sid,
        "title": meta.title or conversations.fallback_title(sid),
        "createdAt": session.created_at,
        "expiresAt": _iso(_created_epoch(session.created_at) + SESSION_TTL_SECONDS),
        "lastActivityAt": last.ts if last else session.created_at,
        "status": _status(session, staffer_active, time.time()),
        "waitingDrafts": waiting,
        "pinned": meta.pinned,
        "group": meta.group,
        "archived": meta.archived,
    }


def _matches(sid: str, title: str, q: str) -> bool:
    """Case-insensitive search over the title and the staffer's own messages (never the agent's)."""
    needle = q.casefold()
    return needle in title.casefold() or any(needle in text.casefold() for text in conversations.staffer_messages(sid))


@router.get("/sessions")
def list_sessions(
    q: str | None = Query(default=None, max_length=200),
    archived: bool = False,
    staff: StaffIdentity = Depends(require_staff),
) -> dict:
    """The caller's own conversations: pinned first, then by last activity. Nobody can list another staffer's.

    Deleted conversations never appear; archived ones only with `?archived=1` (and then only those).
    """
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    active = staffer is not None and staffer.active
    waiting = Counter(draft.sid for _, draft in store.pending_drafts_for(staff.username))
    kept = [
        (sid, session, meta)
        for sid, session in store.sessions_for(staff.username)
        if not (meta := conversations.get_meta(sid, staff.username)).deleted
    ]
    rows = [_row(sid, session, meta, active, waiting[sid]) for sid, session, meta in kept if meta.archived == archived]
    if q and q.strip():
        rows = [r for r in rows if _matches(r["sid"], r["title"], q.strip())]
    rows.sort(key=lambda r: r["lastActivityAt"], reverse=True)
    rows.sort(key=lambda r: not r["pinned"])  # stable: pinned first, each part still newest first
    groups = sorted({meta.group for _, _, meta in kept if meta.group})
    return {"sessions": rows, "groups": groups}


class ConversationPatch(BaseModel):
    """What the staffer may change in the sidebar. Never the owner, the status or the delete mark."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    pinned: StrictBool | None = None
    group: str | None = Field(default=None, max_length=200)
    archived: StrictBool | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = conversations.plain_text(value)
        if not 1 <= len(value) <= 60:
            raise ValueError("a title is 1 to 60 characters")
        return value

    @field_validator("group")
    @classmethod
    def _group(cls, value: str | None) -> str | None:
        value = conversations.plain_text(value or "")
        if len(value) > 30:
            raise ValueError("a group name is at most 30 characters")
        return value or None

    @model_validator(mode="after")
    def _something(self) -> "ConversationPatch":
        if not self.model_fields_set:
            raise ValueError("nothing to change")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("a title cannot be removed, only renamed")
        return self


def _own_conversation(sid: str, staff: StaffIdentity) -> tuple[SessionItem, conversations.ConversationMeta]:
    """The caller's own, not deleted conversation; anything else is 404, as if it did not exist."""
    session = get_gateway().store.get_session(sid)
    if session is None or session.acting_for != staff.username:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    meta = conversations.get_meta(sid, staff.username)
    if meta.deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    return session, meta


@router.patch("/sessions/{sid}")
def update_conversation(sid: str, body: ConversationPatch, staff: StaffIdentity = Depends(require_staff)) -> dict:
    session, meta = _own_conversation(sid, staff)
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    if "title" in changes:
        changes["title_source"] = "user"  # automatic titles never replace the staffer's own
    meta = meta.model_copy(update=changes)
    conversations.put_meta(sid, meta)
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    waiting = sum(1 for _, draft in store.pending_drafts_for(staff.username) if draft.sid == sid)
    return _row(sid, session, meta, staffer is not None and staffer.active, waiting)


@router.delete("/sessions/{sid}")
def delete_conversation(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """Soft delete: hidden from the staffer, revoked, its agent stopped. Events, drafts and the chat stay
    in the table for the audit log."""
    session, meta = _own_conversation(sid, staff)
    store = get_gateway().store
    revoked = session.status == "active"
    if revoked:
        store.put_session(sid, session.model_copy(update={"status": "revoked"}))
    conversations.put_meta(sid, meta.model_copy(update={"deleted": True}))
    supervisor = get_supervisor()
    if supervisor:
        supervisor.stop(sid, "conversation deleted")
    return {"sid": sid, "deleted": True, "revoked": revoked}


@router.get("/sessions/{sid}")
def read_session(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """The session's own staffer, or an admin, sees its status. Anyone else gets 404, as if it did not exist."""
    store = get_gateway().store
    session = store.get_session(sid)
    if session is None or (session.acting_for != staff.username and "admin" not in staff.groups):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    if "admin" not in staff.groups and conversations.get_meta(sid, staff.username).deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")  # admins keep it for the audit
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
    clock = time.time()
    if clock >= _created_epoch(session.created_at) + SESSION_TTL_SECONDS:  # the same instant the console shows
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Session expired; start a new one")
    now = int(clock)
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
