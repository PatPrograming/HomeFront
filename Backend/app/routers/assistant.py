"""The chat console: the staffer talks to the agent, reviews held drafts and decides what it may do alone.

Staffer routes need the staffer's Cognito login. The agent's two routes (`/agent/messages`) need its
mandate token, so the agent only ever reads and answers the chat of the session it was issued for,
and loses the chat the moment the session is revoked, like every tool call.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from identity_gateway.authz import AuthzRequest
from identity_gateway.autonomy import CATEGORIES, CATEGORY_TOOL, LABELS, LOCKED, reviews, view
from identity_gateway.gateway import PLATFORMS, REVISABLE_FIELDS, SOCIAL_TOOLS, DraftAlreadyDecided, get_gateway
from identity_gateway.tokens import InvalidToken, verify_mandate_token
from mandate_contracts import TOOLS, MandateClaims, cedar
from mandate_contracts.ddb import ChatMessageItem, DraftItem
from mandate_contracts.roles import role_reason

try:
    from app.core.cognito import StaffIdentity, require_admin, require_staff
except ModuleNotFoundError as exc:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import StaffIdentity, require_admin, require_staff

from app.routers.sessions import SESSION_TTL_SECONDS, _created_epoch  # noqa: E402  (same session clock as the broker)
from app.routers.draft_rank import rank_draft  # noqa: E402
from app.services import conversations  # noqa: E402
from app.services import agent_failures  # noqa: E402
from app.services.agent_supervisor import AgentStartError, RetryRefused, credentials_mode, get_supervisor  # noqa: E402

router = APIRouter(tags=["assistant"])
bearer = HTTPBearer(auto_error=False)

MAX_MESSAGE_CHARS = 4000
# What the staffer may change at approval time. Never the recipient or the account: those are
# what Cedar checked. (PATCH can also change `to` and `platform`, with a fresh Cedar check.)
EDITABLE_FIELDS = {
    "email_send": ("subject", "body"),
    "email_send_list": ("subject", "body"),
    "social_post": ("text",),
    "social_reply": ("text",),
}
MAX_REVISE_CHARS = 500
PLATFORM_LABELS = {"x": "X", "bluesky": "Bluesky", "linkedin": "LinkedIn", "facebook": "Facebook"}
PLATFORM_OPTIONS = [{"id": platform, "label": PLATFORM_LABELS[platform]} for platform in PLATFORMS]


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    # The version the staffer was looking at; a newer one (an edit, an agent revision) makes this a 409.
    expected_version: int | None = Field(default=None, alias="expectedVersion")


class ApproveIn(DecideIn):  # a `to` or `account` in here is a 422, not ignored
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)
    text: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)


class DraftEditIn(BaseModel):
    """Every field the tools have; the route 422s on any the draft's own tool does not let the staffer change."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    expected_version: int = Field(alias="expectedVersion")
    subject: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)
    to: str | None = Field(default=None, max_length=320)
    text: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)
    platform: Literal[PLATFORMS] | None = None


class ReviseIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=MAX_REVISE_CHARS)


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
    if session is None or session.acting_for != staff.username or conversations.is_deleted(sid, staff.username):
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


def _draft_view(draft_id: str, draft: DraftItem) -> dict:
    editable = REVISABLE_FIELDS[draft.tool]
    return {
        "id": draft_id,
        **draft.model_dump(mode="json"),
        "categoryLabel": LABELS.get(draft.category, draft.category),
        "recipient": draft.args.get("to"),
        "platform": draft.args.get("platform"),
        "editable": list(editable),
        "platformOptions": PLATFORM_OPTIONS if "platform" in editable else [],
    }


def _require_current(draft: DraftItem, expected_version: int | None) -> None:
    if expected_version is not None and draft.version != expected_version:
        raise HTTPException(status.HTTP_409_CONFLICT, "The draft changed since you opened it")


def _refuse_if_not_allowed(draft: DraftItem, args: dict[str, str]) -> None:
    """Ask Cedar about the edited recipient or platform now, so a doomed edit is refused instead of failing at approval.

    Builds the same request as `Gateway.send_draft`, which still checks again when the staffer approves.
    """
    gateway = get_gateway()
    staffer = gateway.store.get_staffer(draft.acting_for)
    params = TOOLS[draft.tool].input_model.model_validate(args)
    decision = gateway.authorizer.is_authorized(
        AuthzRequest(
            agent_id=draft.agent,
            office=draft.office,
            mode="official",
            role=staffer.office_role or "",
            tool=draft.tool,
            resource=cedar.tool_entity(draft.tool),
            context=cedar.build_context(
                delegatorActive=True,
                recipientKnown=draft.tool == "email_send" and gateway._recipient_known(params.to, draft.office),
                account=params.account if draft.tool in SOCIAL_TOOLS else "",
            ),
        )
    )
    if decision.allowed:
        return
    if any(policy.startswith("role-") for policy in decision.policy_ids):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Not part of your role: {role_reason(staffer.office_role, draft.tool)}")
    raise HTTPException(status.HTTP_403_FORBIDDEN, "The office's rules do not allow that recipient or account")


def _count_review(staffer_id: str, category: str, outcome: Literal["approved_unchanged", "edited", "rejected"]) -> None:
    store = get_gateway().store
    item = store.get_autonomy(staffer_id, category)
    store.put_autonomy(staffer_id, category, item.model_copy(update={outcome: getattr(item, outcome) + 1}))


def require_agent_session(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> MandateClaims:
    """The agent's mandate or aa-auth token, for a session that is still active."""
    gateway = get_gateway()
    token = creds.credentials if creds else ""
    try:
        claims = verify_mandate_token(token, gateway.secret)
    except InvalidToken:
        try:
            import jwt
            from aauth import verify_aa_auth_jwt
            from identity_gateway.aauth_bridge import claims_from_aa_auth

            header = jwt.get_unverified_header(token)
            if header.get("typ") != "aa-auth+jwt":
                raise InvalidToken("not aa-auth")
            claims = claims_from_aa_auth(verify_aa_auth_jwt(token, secret=gateway.secret))
        except Exception:
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
    if session is None or session.acting_for != staff.username or conversations.is_deleted(sid, staff.username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown session")
    page = _chat_page(sid, after)
    if "admin" in staff.groups:  # admins also see what went wrong technically; staffers see plain words
        details = agent_failures.details_for(sid)
        for message in page["messages"]:
            if message["id"] in details:
                message["detail"] = details[message["id"]]
    return page


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
        raise _start_failed(error) from None


def _start_failed(error: AgentStartError) -> HTTPException:
    """503 with the reason; for the cases the console can act on, a code and the actions it offers."""
    message = str(error) if error.code == "agent_not_set_up" else f"The assistant could not start: {error}"
    detail = {"message": message, "code": error.code, "actions": error.actions} if error.code else message
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail)


@router.post("/sessions/{sid}/agent/retry")
def retry_agent(sid: str, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """"Try again" after the start cap or a missing agent secret: owner only, rate-limited, audited."""
    _own_active_session(sid, staff)
    supervisor = get_supervisor()
    if supervisor is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "auto-start is off: start the agent from a terminal")
    session = get_gateway().store.get_session(sid)
    try:
        agent, left = supervisor.retry(sid, staff.username, _created_epoch(session.created_at) + SESSION_TTL_SECONDS)
    except RetryRefused as refused:
        detail = {"message": str(refused), "code": "agent_retry_refused", "actions": refused.actions}
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail) from None
    except AgentStartError as error:
        raise _start_failed(error) from None
    return {"sid": sid, "agent": agent, "retriesLeft": left}


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


class AgentMessageIn(MessageIn):
    detail: str | None = Field(default=None, max_length=300)  # e.g. "ClientError: ThrottlingException"; admins only


@router.post("/agent/messages")
def agent_send_message(body: AgentMessageIn, claims: MandateClaims = Depends(require_agent_session)) -> dict:
    message = _post(claims.sid, "assistant", body.text)
    if body.detail:
        agent_failures.save_detail(claims.sid, message["id"], body.detail)
    return message


# --- review queue -------------------------------------------------------------


@router.get("/drafts")
def list_drafts(sid: str | None = None, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """Every draft waiting for the caller, or only one conversation's with `?sid=`."""
    store = get_gateway().store
    now = datetime.now(timezone.utc)
    drafts = []
    for draft_id, draft in store.pending_drafts_for(staff.username):
        if sid is not None and draft.sid != sid:
            continue
        reply_to = draft.args.get("in_reply_to")
        email = store.get_email(reply_to) if reply_to else None
        drafts.append(
            {
                **_draft_view(draft_id, draft),
                **rank_draft(draft, email, now),
            }
        )
    # Most urgent first; the oldest first among equals. The console can also show them by time.
    drafts.sort(key=lambda d: (-d["score"], d["createdAt"]))
    return {"drafts": drafts}


@router.patch("/drafts/{draft_id}")
def edit_draft(draft_id: str, body: DraftEditIn, staff: StaffIdentity = Depends(require_staff)):
    draft = _own_pending_draft(draft_id, staff)
    changes = body.model_dump(exclude_none=True, exclude={"expected_version"})
    editable = REVISABLE_FIELDS[draft.tool]
    if not changes or not changes.keys() <= set(editable):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"You can change only: {', '.join(editable)}")
    if draft.version != body.expected_version:
        return JSONResponse({"error": "stale_version", "draft": _draft_view(draft_id, draft)}, status.HTTP_409_CONFLICT)
    args = {**draft.args, **changes}
    if changes.keys() & {"to", "platform"}:
        _refuse_if_not_allowed(draft, args)
    edited = draft.model_copy(update={"args": args, "version": draft.version + 1, "edited": True})
    gateway = get_gateway()
    # Written only while still pending, so an edit can never undo an approval or rejection that just happened.
    if not gateway.store.decide_draft(draft_id, edited):
        raise HTTPException(status.HTTP_409_CONFLICT, "Draft already decided")
    now = int(time.time())
    claims = MandateClaims(
        sub=draft.agent, act=draft.acting_for, office=draft.office, mode="official", sid=draft.sid, iat=now, exp=now
    )
    gateway._record(claims, draft.tool, args.get("to") or args.get("account"), "ALLOW", "edited by staff")
    return _draft_view(draft_id, edited)


@router.post("/drafts/{draft_id}/revise", status_code=status.HTTP_202_ACCEPTED)
def revise_draft(draft_id: str, body: ReviseIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """Ask the assistant to rewrite the draft; it answers with `draft_revise`, which clears `revising`."""
    draft = _own_pending_draft(draft_id, staff)
    if draft.revising:
        raise HTTPException(status.HTTP_409_CONFLICT, "The assistant is already revising this draft")
    _own_active_session(draft.sid, staff)
    _ensure_agent(draft.sid, staff)
    if not get_gateway().store.decide_draft(draft_id, draft.model_copy(update={"revising": True})):
        raise HTTPException(status.HTTP_409_CONFLICT, "Draft already decided")
    _post(draft.sid, "user", f"Revise draft {draft_id}: {body.instruction}")
    return {"id": draft_id, "revising": True}


@router.post("/drafts/{draft_id}/approve")
def approve_draft(draft_id: str, body: ApproveIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    draft = _own_pending_draft(draft_id, staff)
    _require_current(draft, body.expected_version)
    text = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in EDITABLE_FIELDS[draft.tool]}
    try:
        outcome = get_gateway().send_draft(draft_id, draft, text)
    except DraftAlreadyDecided:
        raise HTTPException(status.HTTP_409_CONFLICT, "Draft already decided") from None
    if outcome.result and outcome.result["status"] == "needs_connection":  # the draft stays pending
        return {"id": draft_id, **outcome.result}
    if not outcome.allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, outcome.deny_text())
    edited = draft.edited or get_gateway().store.get_draft(draft_id).edited  # send_draft only sees this call's text
    _count_review(staff.username, draft.category, "edited" if edited else "approved_unchanged")
    if outcome.result and outcome.result.get("status") == "share_link":  # X not connected: the staffer posts on X
        return {"id": draft_id, "status": "share_link", "url": outcome.result["url"], "edited": edited}
    return {"id": draft_id, "status": "sent", "edited": edited}


@router.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: str, body: DecideIn | None = None, staff: StaffIdentity = Depends(require_staff)) -> dict:
    draft = _own_pending_draft(draft_id, staff)
    _require_current(draft, body.expected_version if body else None)
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


def _within_role(category: str, staff: StaffIdentity) -> None:
    """Letting the agent send a category alone is only for a tool the staffer's own role may use.

    Cedar denies that tool before any grant or "auto" is read, so this is not the guarantee; it stops
    the API from recording a permission the staffer does not have. The role is read from DynamoDB.
    """
    store = get_gateway().store
    staffer = store.get_staffer(staff.username)
    role = staffer.office_role if staffer else None
    tool = CATEGORY_TOOL[category]
    if tool not in store.get_role_tools(role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Not part of your role: {role_reason(role, tool)}")


@router.get("/autonomy")
def list_autonomy(staff: StaffIdentity = Depends(require_staff)) -> dict:
    return {"categories": [view(c, _autonomy(staff, c)).as_json() for c in CATEGORIES]}


@router.put("/autonomy/{category}")
def set_autonomy(category: str, body: AutonomyIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    _known_category(category)
    if body.level == "auto" and category in LOCKED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This category always needs your review")
    if body.level == "auto":
        _within_role(category, staff)
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
    _within_role(body.category, staff)
    session = get_gateway().store.get_session(sid)
    expires_at = int(_created_epoch(session.created_at)) + SESSION_TTL_SECONDS  # the grant ends with the session
    get_gateway().store.put_grant(sid, body.category, staff.username, datetime.now(timezone.utc).isoformat(), expires_at)
    return {"sid": sid, "category": body.category, "label": LABELS[body.category], "scope": "this session only"}


@router.get("/assistant/status")
def assistant_status(admin: StaffIdentity = Depends(require_admin)) -> dict:
    """For admins' Technical details: does the console start the agent, and with which AWS credentials."""
    return {"autostart": get_supervisor() is not None, "agentCredentials": credentials_mode()}


# --- plan card: which helper agents the assistant will use, approved before it acts -----------
# The agent proposes; the server writes the card from the staffer's role, so the agent cannot
# describe a permission it does not have. Only this staffer route can approve, and chat text
# saying "approved" is not an approval: the agent waits for the message this route posts.

PLAN_AGENTS = {"research": "Research agent", "social": "Social media agent", "email": "Email agent"}
PLAN_TOOL_WORDS = {
    "datagov_search": "search government data (data.gov)",
    "web_scrape": "read public web pages",
    "census_query": "look up Census data",
    "bls_series": "look up labor statistics (BLS)",
    "openfda_search": "search openFDA",
    "eia_query": "look up energy data (EIA)",
    "nasa_search": "search NASA images",
    "noaa_climate": "look up NOAA climate data",
    "nps_parks": "look up National Parks",
    "fec_search": "search FEC campaign finance",
    "epa_search": "search EPA facilities",
    "fcc_search": "search FCC licenses",
    "usaspending_search": "search federal contract awards",
    "sam_entity_lookup": "look up vendors in SAM.gov",
    "inbox_read": "read the office inbox",
    "case_read": "read a constituent case",
    "report_save": "save a report for staff",
    "social_post": "write one social post draft",
    "social_reply": "write one social reply draft",
    "email_send": "write one email draft",
    "email_send_list": "write one mailing-list email draft",
}
OUTBOUND_TOOLS = {"social_post", "social_reply", "email_send", "email_send_list"}


class PlanStepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: Literal["research", "social", "email"]
    purpose: str = Field(min_length=1, max_length=300)
    tools: list[str] = Field(max_length=20)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStepIn] = Field(min_length=1, max_length=5)


class PlanDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "cancel"]


def build_plan_steps(steps: list[PlanStepIn], role_tools: set[str]) -> list[dict]:
    """The card's steps: each tool in plain words, only if the staffer's role has it."""
    card = []
    for step in steps:
        allowed, dropped = [], []
        for raw in dict.fromkeys(t.replace(".", "_") for t in step.tools):
            if raw in PLAN_TOOL_WORDS and raw in role_tools:
                outbound = raw in OUTBOUND_TOOLS
                words = PLAN_TOOL_WORDS[raw] + (" (held for your review)" if outbound else "")
                allowed.append({"name": raw, "label": words, "outbound": outbound})
            else:
                dropped.append(raw)
        card.append(
            {
                "agent": step.agent,
                "label": PLAN_AGENTS[step.agent],
                "purpose": step.purpose,
                "tools": allowed,
                "readOnly": not any(t["outbound"] for t in allowed),
                "dropped": dropped,  # shown as "not allowed for your role"
            }
        )
    return card


@router.post("/agent/plans")
def agent_propose_plan(body: PlanIn, claims: MandateClaims = Depends(require_agent_session)) -> dict:
    store = get_gateway().store
    staffer = store.get_staffer(claims.act)
    steps = build_plan_steps(body.steps, set(store.get_role_tools(staffer.office_role)))
    plan_id = str(uuid.uuid4())
    plan = {"id": plan_id, "steps": steps, "status": "pending"}
    message = ChatMessageItem(
        id=str(uuid.uuid4()),
        ts=datetime.now(timezone.utc).isoformat(),
        role="assistant",
        text="To do this I'll use these helper agents. Approve the plan and I'll start; nothing is sent without your review.",
        plan=plan,
    )
    tools = list(dict.fromkeys(t["name"] for step in steps for t in step["tools"]))  # already within the role
    store.put_plan(claims.sid, plan_id, {"status": "pending", "actingFor": claims.act, "msgTs": message.ts,
                                          "msgId": message.id, "tools": tools})
    store.put_chat_message(claims.sid, message)
    return {"status": "awaiting_approval", "plan_id": plan_id, "steps": steps}


@router.post("/sessions/{sid}/plans/{plan_id}")
def decide_plan(sid: str, plan_id: str, body: PlanDecisionIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    _own_active_session(sid, staff)
    store = get_gateway().store
    item = store.get_plan(sid, plan_id)
    if item is None or item.get("actingFor") != staff.username:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown plan")
    new_status = "approved" if body.decision == "approve" else "cancelled"
    if item.get("status") != "pending" or not store.decide_plan(sid, plan_id, new_status):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Plan already {item.get('status')}")
    # Rewrite the card's message so a reload shows the decision.
    for message in store.chat_since(sid, None)[0]:
        if message.id == item.get("msgId") and message.plan:
            store.put_chat_message(sid, message.model_copy(update={"plan": {**message.plan, "status": new_status}}))
            break
    session = store.get_session(sid)
    now = int(time.time())
    claims = MandateClaims(sub=session.agent, act=staff.username, office=session.office, mode="official", sid=sid,
                           iat=now, exp=now)
    approved = body.decision == "approve"
    if approved:  # from now on the gateway denies every tool outside this plan, in this session
        store.set_plan_tools(sid, list(item.get("tools", [])))
    get_gateway()._record(claims, "plan_decide", f"plan {plan_id}", "ALLOW" if approved else "DENY",
                          "approved by staff" if approved else "cancelled by staff")
    if approved:
        _ensure_agent(sid, staff)  # the agent may have stopped while the card waited; it must see the approval
    _post(sid, "user", "Approved the plan." if approved else "Cancelled the plan.")
    return {"id": plan_id, "status": new_status, "planTools": store.plan_tools(sid)}
