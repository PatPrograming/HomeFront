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

from atlas_enricher import AnnotateRequest
from atlas_enricher.enricher import AnnotateEvent
from identity_gateway.gateway import get_gateway
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import TOOLS, TTL_SECONDS, DecisionEvent, MandateClaims
from mandate_contracts.ddb import SessionItem
from mandate_contracts.roles import role_label
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

def allowed_tools(office_role: str | None) -> list[str]:
    """What the console lists as allowed: the role's current list from DynamoDB. Display only: Cedar's
    role policies are what enforce it."""
    role_tools = get_gateway().store.get_role_tools(office_role)
    return [name for name in TOOLS if name in role_tools]


REVOKE_TOOL = "session_revoke"  # the audit event's tool name when a staffer stops their own task


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _created_epoch(created_at: str) -> float:
    """Session start as epoch seconds; an unreadable timestamp counts as long expired."""
    try:
        return datetime.fromisoformat(created_at).timestamp()
    except ValueError:
        return 0.0


class NewSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Carry on a conversation whose 1-hour session has ended: the caller's own, expired, never revoked.
    continues: str | None = Field(default=None, min_length=1, max_length=100)


def _refuse(code: int, message: str, reason: str) -> HTTPException:
    return HTTPException(code, {"message": message, "code": reason})


def _check_continuable(old_sid: str, staff: StaffIdentity, now: float) -> bool:
    """Raise unless `old_sid` may be continued by this staffer. Returns whether it had one-time grants."""
    store = get_gateway().store
    old = store.get_session(old_sid)
    if old is None or old.acting_for != staff.username or conversations.is_deleted(old_sid, staff.username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")  # never someone else's
    if old.status != "active":
        raise _refuse(status.HTTP_403_FORBIDDEN, "A revoked session can't be continued.", "revoked")
    if now < _created_epoch(old.created_at) + SESSION_TTL_SECONDS:
        raise _refuse(status.HTTP_409_CONFLICT, "This session is still running; keep using it.", "still_active")
    if conversations.get_meta(old_sid, staff.username).continued_by:
        raise _refuse(status.HTTP_409_CONFLICT, "This conversation was already continued; use its newest session.",
                      "already_continued")
    return bool(store.grants_for(old_sid))


@router.post("/sessions")
def create_session(body: NewSessionIn | None = None, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """A new session. With `continues`, the next link of an expired conversation: still a fresh 1-hour session
    with fresh mandate tokens, and the old session's one-time grants do not carry over."""
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    old_sid = body.continues if body else None
    if staffer is None or not staffer.active:
        if old_sid:  # same refusal as a revoked session, so the console offers the same way out
            raise _refuse(status.HTTP_403_FORBIDDEN, "A revoked session can't be continued.", "revoked")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staffer is not active")
    started = time.time()
    sid = str(uuid.uuid4())
    grants_reset = False
    if old_sid:
        grants_reset = _check_continuable(old_sid, staff, started)
        if not conversations.claim_continuation(old_sid, sid, staff.username):  # a second tab got there first
            raise _refuse(status.HTTP_409_CONFLICT, "This conversation was already continued; use its newest session.",
                          "already_continued")
    session = SessionItem(
        status="active",
        agent=AGENT_ID,
        acting_for=staff.username,
        office=staffer.office,  # office comes from the STAFFER item, not from Cognito or the client
        mode="official",
        created_at=datetime.fromtimestamp(started, timezone.utc).isoformat(),  # sub-second, so tabs sort newest first
    )
    store.put_session(sid, session)
    card = {
        "sid": sid,
        "agent": session.agent,
        "actingFor": session.acting_for,
        "office": session.office,
        "mode": session.mode,
        # The role is read from the STAFFER item; nothing the client sends can set it.
        "role": staffer.office_role,
        "roleLabel": role_label(staffer.office_role),
        "allowedTools": allowed_tools(staffer.office_role),
        "expiresAt": _iso(started + SESSION_TTL_SECONDS),
    }
    if old_sid:
        conversations.link_back(sid, old_sid, staff.username)
        card |= {"continuesFrom": old_sid, "grantsReset": grants_reset}
    return card


def _status(session: SessionItem, staffer_active: bool, now: float) -> str:
    if session.status != "active" or not staffer_active:
        return "revoked"
    return "expired" if now >= _created_epoch(session.created_at) + SESSION_TTL_SECONDS else "active"


def _chains(staffer_id: str) -> list[tuple[list[str], dict[str, SessionItem], conversations.ConversationMeta]]:
    """The staffer's conversations: (sids oldest first, sessions by sid, the root's metadata)."""
    sessions = dict(get_gateway().store.sessions_for(staffer_id))
    metas = {sid: conversations.get_meta(sid, staffer_id) for sid in sessions}
    chains = []
    for root, meta in metas.items():
        if meta.continues_from:  # not a root: it is listed with its chain
            continue
        sids = [root]
        while (following := metas[sids[-1]].continued_by) in sessions and len(sids) < conversations.MAX_CHAIN:
            sids.append(following)
        chains.append((sids, sessions, meta))
    return chains


def _row(sids: list[str], sessions: dict[str, SessionItem], meta: conversations.ConversationMeta,
         staffer_active: bool, waiting: Counter) -> dict:
    """One sidebar row per conversation: the newest session, with the title and marks of the first."""
    store = get_gateway().store
    newest = sessions[sids[-1]]
    last = [m.ts for sid in sids if (m := store.last_chat_message(sid))]
    return {
        "sid": sids[-1],
        "title": meta.title or conversations.fallback_title(sids[0]),
        "createdAt": newest.created_at,
        "expiresAt": _iso(_created_epoch(newest.created_at) + SESSION_TTL_SECONDS),
        "lastActivityAt": max(last) if last else newest.created_at,
        "status": _status(newest, staffer_active, time.time()),
        "waitingDrafts": sum(waiting[sid] for sid in sids),
        "pinned": meta.pinned,
        "group": meta.group,
        "archived": meta.archived,
        "earlier": sids[-2::-1],  # older sessions, newest first; each readable with GET /sessions/{sid}/messages
    }


def _matches(sids: list[str], title: str, q: str) -> bool:
    """Case-insensitive search over the title and the staffer's own messages (never the agent's), in every link."""
    needle = q.casefold()
    return needle in title.casefold() or any(
        needle in text.casefold() for sid in sids for text in conversations.staffer_messages(sid)
    )


@router.get("/sessions")
def list_sessions(
    q: str | None = Query(default=None, max_length=200),
    archived: bool = False,
    staff: StaffIdentity = Depends(require_staff),
) -> dict:
    """The caller's own conversations, one row each: pinned first, then by last activity. Nobody can list another
    staffer's. Deleted conversations never appear; archived ones only with `?archived=1` (and then only those).
    """
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    active = staffer is not None and staffer.active
    waiting = Counter(draft.sid for _, draft in store.pending_drafts_for(staff.username))
    kept = [(sids, sessions, meta) for sids, sessions, meta in _chains(staff.username) if not meta.deleted]
    rows = [(_row(sids, sessions, meta, active, waiting), sids) for sids, sessions, meta in kept if meta.archived == archived]
    if q and q.strip():
        rows = [(r, sids) for r, sids in rows if _matches(sids, r["title"], q.strip())]
    found = [r for r, _ in rows]
    found.sort(key=lambda r: r["lastActivityAt"], reverse=True)
    found.sort(key=lambda r: not r["pinned"])  # stable: pinned first, each part still newest first
    groups = sorted({meta.group for _, _, meta in kept if meta.group})
    return {"sessions": found, "groups": groups}


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
        for name in ("title", "pinned", "archived"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


def _own_conversation(sid: str, staff: StaffIdentity) -> tuple[SessionItem, str]:
    """The caller's own, not deleted conversation, and its root (first session); anything else is 404."""
    session = get_gateway().store.get_session(sid)
    if session is None or session.acting_for != staff.username:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    root = conversations.root_of(sid, staff.username)
    if conversations.get_meta(root, staff.username).deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    return session, root


def _chain_of(root: str, staffer_id: str):
    return next((chain for chain in _chains(staffer_id) if chain[0][0] == root), None)


@router.patch("/sessions/{sid}")
def update_conversation(sid: str, body: ConversationPatch, staff: StaffIdentity = Depends(require_staff)) -> dict:
    _, root = _own_conversation(sid, staff)
    changes = {field: getattr(body, field) for field in body.model_fields_set}
    if "title" in changes:
        changes["title_source"] = "user"  # automatic titles never replace the staffer's own
    conversations.update_meta(root, staff.username, changes)  # the root carries the marks for the whole chain
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    waiting = Counter(draft.sid for _, draft in store.pending_drafts_for(staff.username))
    sids, sessions, meta = _chain_of(root, staff.username)
    return _row(sids, sessions, meta, staffer is not None and staffer.active, waiting)


@router.delete("/sessions/{sid}")
def delete_conversation(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """Soft delete: hidden from the staffer, revoked, its agent stopped. Events, drafts and the chat stay
    in the table for the audit log."""
    _, root = _own_conversation(sid, staff)
    store = get_gateway().store
    sids, sessions, _ = _chain_of(root, staff.username)
    revoked = False
    supervisor = get_supervisor()
    for link in sids:  # every session of the conversation
        if sessions[link].status == "active":
            store.put_session(link, sessions[link].model_copy(update={"status": "revoked"}))
            revoked = revoked or link == sid
        if supervisor:
            supervisor.stop(link, "conversation deleted")
    conversations.update_meta(root, staff.username, {"deleted": True})
    return {"sid": sid, "deleted": True, "revoked": revoked}


@router.post("/sessions/{sid}/revoke")
def revoke_session(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """The staffer stops their own running task. Only the owner's Cognito login can do it (not an admin, not
    the agent's mandate token). Same fail-closed path as offboarding, for one conversation: guarded() and the
    mandate broker both read the session status, so the very next tool call or mint is denied."""
    session, _ = _own_conversation(sid, staff)
    store = get_gateway().store
    already = session.status != "active"
    if not already:
        store.put_session(sid, session.model_copy(update={"status": "revoked"}))
        _record_stop(sid, session, staff.username)
    supervisor = get_supervisor()
    if supervisor:  # the agent would be denied at its next call anyway; this ends the process now
        supervisor.stop(sid, "stopped by staffer")
    return {"sid": sid, "status": "revoked", "alreadyRevoked": already}


def _record_stop(sid: str, session: SessionItem, staffer: str) -> None:
    """One audit event for the stop, labelled like the gateway's own revocations."""
    gateway = get_gateway()
    atlas = gateway.enricher.annotate(AnnotateRequest(event=AnnotateEvent.revoke)).technique_ids
    gateway.store.put_event(
        DecisionEvent(
            id=str(uuid.uuid4()),
            ts=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            sid=sid,
            agent=session.agent,
            acting_for=session.acting_for,
            office=session.office,
            tool=REVOKE_TOOL,
            resource=f"stopped by {staffer}",
            decision="DENY",
            reason="session revoked / delegator inactive",
            atlas=atlas,
        )
    )


@router.get("/sessions/{sid}")
def read_session(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """The session's own staffer, or an admin, sees its status. Anyone else gets 404, as if it did not exist."""
    store = get_gateway().store
    session = store.get_session(sid)
    if session is None or (session.acting_for != staff.username and "admin" not in staff.groups):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    if "admin" not in staff.groups and conversations.is_deleted(sid, staff.username):
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
        # Once a plan is approved, the session is limited to its tools (the gateway enforces it); else None.
        "planTools": store.plan_tools(sid),
    }


@router.post("/sessions/{sid}/mandate")
def issue_mandate(sid: str, agent: AgentClientIdentity = Depends(require_agent_client)) -> dict:
    """Legacy HS256 mandate mint. Prefer POST /oauth/token (ID-JAG + aa-auth) when available."""
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
    return {
        "mandateToken": mint_mandate_token(claims, gateway.secret),
        "expiresAt": _iso(claims.exp),
        "auth_protocol": "legacy_mandate",
        "oauth_token_endpoint": "/api/v1/oauth/token",
    }
