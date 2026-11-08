"""The chat console: the staffer talks to the agent, reviews held drafts and decides what it may do alone.

Staffer routes need the staffer's Cognito login. The agent's two routes (`/agent/messages`) need its
mandate token, so the agent only ever reads and answers the chat of the session it was issued for,
and loses the chat the moment the session is revoked, like every tool call.
"""

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from identity_gateway.autonomy import CATEGORIES, LABELS, LOCKED, reviews, view
from identity_gateway.gateway import DraftAlreadyDecided, get_gateway
from identity_gateway.tokens import InvalidToken, verify_mandate_token
from mandate_contracts import MandateClaims
from mandate_contracts.ddb import ChatMessageItem, DraftItem

try:
    from app.core.cognito import StaffIdentity, require_staff
except ModuleNotFoundError as exc:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import StaffIdentity, require_staff

from app.routers.sessions import SESSION_TTL_SECONDS, _created_epoch  # noqa: E402  (same session clock as the broker)
from app.services import conversations  # noqa: E402
from app.services.agent_supervisor import AgentStartError, get_supervisor  # noqa: E402

router = APIRouter(tags=["assistant"])
bearer = HTTPBearer(auto_error=False)

MAX_MESSAGE_CHARS = 4000
# What the staffer may change before approving. Never the recipient or the account: those are
# what Cedar checked.
EDITABLE_FIELDS = {"email_send": ("subject", "body"), "social_post": ("text",)}


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a `to` or `account` in here is a 422, not ignored
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)
    text: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)


class AutonomyIn(BaseModel):
    level: Literal["review", "auto"]


def _post(sid: str, role: Literal["user", "assistant"], text: str) -> dict:
    message = ChatMessageItem(id=str(uuid.uuid4()), ts=datetime.now(timezone.utc).isoformat(), role=role, text=text)
    get_gateway().store.put_chat_message(sid, message)
    return message.model_dump(mode="json")


def _chat_page(sid: str, after: str | None) -> dict:
    messages, cursor = get_gateway().store.chat_since(sid, after)
    return {"messages": [m.model_dump(mode="json") for m in messages], "next": cursor}


def _own_active_session(sid: str, staff: StaffIdentity) -> None:
    """Only the session's own staffer chats in it; anyone else gets 404, as if it did not exist."""
    store = get_gateway().store
    session = store.get_session(sid)
    if session is None or session.acting_for != staff.username or conversations.get_meta(sid, staff.username).deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    staffer = store.get_staffer(staff.username)
    if session.status != "active" or staffer is None or not staffer.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Session revoked or staffer inactive")


def _own_pending_draft(draft_id: str, staff: StaffIdentity) -> DraftItem:
    draft = get_gateway().store.get_draft(draft_id)
    if draft is None or draft.acting_for != staff.username:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown draft")
    if draft.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Draft already {draft.status}")
    return draft


def _count_review(staffer_id: str, category: str, outcome: Literal["approved_unchanged", "edited", "rejected"]) -> None:
    store = get_gateway().store
    item = store.get_autonomy(staffer_id, category)
    store.put_autonomy(staffer_id, category, item.model_copy(update={outcome: getattr(item, outcome) + 1}))


def require_agent_session(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> MandateClaims:
    """The agent's mandate token, for a session that is still active (same rule as the gateway)."""
    gateway = get_gateway()
    try:
        claims = verify_mandate_token(creds.credentials if creds else "", gateway.secret)
    except InvalidToken:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing mandate token") from None
    session = gateway.store.get_session(claims.sid)
    staffer = gateway.store.get_staffer(claims.act)
    if session is None or session.status != "active" or staffer is None or not staffer.active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Session revoked or staffer inactive")
    return claims


# --- chat: staffer side ---------------------------------------------------------


@router.get("/sessions/{sid}/messages")
def read_messages(sid: str, after: str | None = None, staff: StaffIdentity = Depends(require_staff)) -> dict:
    session = get_gateway().store.get_session(sid)
    if session is None or session.acting_for != staff.username or conversations.get_meta(sid, staff.username).deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    return _chat_page(sid, after)


def _ensure_agent(sid: str, staff: StaffIdentity) -> None:
    """Start the session's chat agent when auto-start is on (HOMEFRONT_AUTOSTART_AGENT=1).

    Runs before the message is stored, so a message the agent can never answer is refused
    (503 with the reason) instead of waiting forever for a reply.
    """
    supervisor = get_supervisor()
    if supervisor is None:
        return
    session = get_gateway().store.get_session(sid)
    ends_at = _created_epoch(session.created_at) + SESSION_TTL_SECONDS
    try:
        supervisor.ensure_running(sid, staff.username, ends_at)
    except AgentStartError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"The assistant could not start: {error}") from None


@router.post("/sessions/{sid}/messages")
def send_message(
    sid: str, body: MessageIn, background: BackgroundTasks, staff: StaffIdentity = Depends(require_staff)
) -> dict:
    _own_active_session(sid, staff)
    _ensure_agent(sid, staff)
    message = _post(sid, "user", body.text)
    background.add_task(conversations.refresh_title, sid, staff.username)  # after the reply, so the chat never waits
    return message


# --- chat: agent side -----------------------------------------------------------


@router.get("/agent/messages")
def agent_read_messages(after: str | None = None, claims: MandateClaims = Depends(require_agent_session)) -> dict:
    return _chat_page(claims.sid, after)


@router.post("/agent/messages")
def agent_send_message(body: MessageIn, claims: MandateClaims = Depends(require_agent_session)) -> dict:
    return _post(claims.sid, "assistant", body.text)


# --- review queue -------------------------------------------------------------


@router.get("/drafts")
def list_drafts(sid: str | None = None, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """Every draft waiting for the caller, or only one conversation's with `?sid=`."""
    drafts = [pair for pair in get_gateway().store.pending_drafts_for(staff.username) if sid is None or pair[1].sid == sid]
    return {
        "drafts": [
            {"id": draft_id, **draft.model_dump(mode="json"), "categoryLabel": LABELS.get(draft.category, draft.category)}
            for draft_id, draft in drafts
        ]
    }


@router.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: str, body: ApproveIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    draft = _own_pending_draft(draft_id, staff)
    text = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in EDITABLE_FIELDS[draft.tool]}
    try:
        outcome = get_gateway().send_draft(draft_id, draft, text)
    except DraftAlreadyDecided:
        raise HTTPException(status.HTTP_409_CONFLICT, "Draft already decided") from None
    if not outcome.allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, outcome.deny_text())
    edited = get_gateway().store.get_draft(draft_id).edited
    _count_review(staff.username, draft.category, "edited" if edited else "approved_unchanged")
    return {"id": draft_id, "status": "sent", "edited": edited}


@router.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    draft = _own_pending_draft(draft_id, staff)
    rejected = draft.model_copy(update={"status": "rejected", "decided_at": datetime.now(timezone.utc).isoformat()})
    if not get_gateway().store.decide_draft(draft_id, rejected):
        raise HTTPException(status.HTTP_409_CONFLICT, "Draft already decided")
    _count_review(staff.username, draft.category, "rejected")
    return {"id": draft_id, "status": "rejected"}


# --- what the agent may do alone ---------------------------------------------------


def _autonomy(staff: StaffIdentity, category: str):
    return get_gateway().store.get_autonomy(staff.username, category)


def _known_category(category: str) -> None:
    if category not in CATEGORIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown category")


@router.get("/autonomy")
def list_autonomy(staff: StaffIdentity = Depends(require_staff)) -> dict:
    return {"categories": [view(c, _autonomy(staff, c)).as_json() for c in CATEGORIES]}


@router.put("/autonomy/{category}")
def set_autonomy(category: str, body: AutonomyIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    _known_category(category)
    if body.level == "auto" and category in LOCKED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This category always needs your review")
    item = _autonomy(staff, category).model_copy(update={"level": body.level})
    get_gateway().store.put_autonomy(staff.username, category, item)
    return view(category, item).as_json()


@router.post("/autonomy/{category}/snooze")
def snooze_suggestion(category: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """'Not now': ask again only after a few more reviews."""
    _known_category(category)
    item = _autonomy(staff, category)
    item = item.model_copy(update={"snoozed_at": reviews(item)})
    get_gateway().store.put_autonomy(staff.username, category, item)
    return view(category, item).as_json()


# --- per-session permission grants ----------------------------------------------
# The agent asks in the chat ("may I send these alone?"); only this route, with the staffer's own
# Cognito login, can say yes. Chat text never grants anything. The grant only skips the review
# queue after Cedar has allowed the call, so a `forbid` policy can never be granted by anyone.


class GrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str


@router.get("/sessions/{sid}/grants")
def list_grants(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    _own_active_session(sid, staff)
    return {"grants": get_gateway().store.grants_for(sid)}


@router.post("/sessions/{sid}/grants")
def grant_for_session(sid: str, body: GrantIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    _own_active_session(sid, staff)
    _known_category(body.category)
    if body.category in LOCKED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This category always needs your review")
    session = get_gateway().store.get_session(sid)
    expires_at = int(_created_epoch(session.created_at)) + SESSION_TTL_SECONDS  # the grant ends with the session
    get_gateway().store.put_grant(sid, body.category, staff.username, datetime.now(timezone.utc).isoformat(), expires_at)
    return {"sid": sid, "category": body.category, "label": LABELS[body.category], "scope": "this session only"}
