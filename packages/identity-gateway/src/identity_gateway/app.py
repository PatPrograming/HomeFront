from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Allow running without editable installs by putting sibling packages on path.
_ROOT = Path(__file__).resolve().parents[3]
for rel in (
    _ROOT / "atlas-enricher" / "src",
    _ROOT / "mcp-tools" / "src",
    Path(__file__).resolve().parents[4] / "packages" / "atlas-enricher" / "src",
    Path(__file__).resolve().parents[4] / "packages" / "mcp-tools" / "src",
):
    if rel.is_dir() and str(rel) not in sys.path:
        sys.path.insert(0, str(rel))

from atlas_enricher import AnnotateRequest, AtlasEnricher  # noqa: E402
from atlas_enricher.enricher import AnnotateEvent  # noqa: E402
from atlas_enricher.router import build_router  # noqa: E402
from mcp_tools import handlers as email_handlers  # noqa: E402
from mcp_tools.handlers import invoke_tool, resolve_sender, ses_identity_status  # noqa: E402
from mcp_tools.registry import get_tool, list_tools, tool_public_view  # noqa: E402

from . import email_admin  # noqa: E402

from .agent_types import (
    AGENT_TYPES,
    DEFAULT_KIND,
    AgentKind,
    agent_type_view,
    disallowed_tools,
    get_agent_type,
)
from .approvals import ApprovalConflict, ApprovalRecord
from .reports import ReportRecord
from .runner import AgentRunner, RunStore, new_run
from .store import (
    AGENT_ID_PATTERN,
    DEFAULT_MAX_STEPS,
    MAX_FROM_NAME,
    MAX_RECIPIENTS,
    MAX_SIGNATURE,
    AgentRecord,
    DuplicateAgentError,
    GatewayStore,
    InvalidEmailSettingsError,
    InvalidRecipientsError,
    normalize_email,
    normalize_email_settings,
)

# Agents live in memory by default; the host app can swap in DynamoDB via
# store.use_agent_repository(...).
store = GatewayStore()
runs = RunStore()
# Set by the host app (see Backend/app/agent_iam.py); None = runs return 503.
runner: AgentRunner | None = None
enricher = AtlasEnricher()


def _save_report(data: dict[str, Any]) -> dict[str, Any]:
    # Looked up per call: the host app may swap in the DynamoDB repository.
    return store.reports.insert(ReportRecord(**data)).view()


email_handlers.set_report_sink(_save_report)

app = FastAPI(
    title="HomeFront Identity Gateway",
    description="Agent workload identity + MCP tool authorization with MITRE ATLAS enrichment",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(build_router(enricher))


class AuthRequest(BaseModel):
    agent_id: str
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    agent_id: str
    agency: str
    tool_grants: list[str]
    token_version: int
    # Trace id for this token's calls in the activity log.
    session_id: str
    atlas: dict[str, Any]


class GrantUpdate(BaseModel):
    tools: list[str]


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# Field limits mirror the check constraints on public.agents.
class CreateAgentRequest(BaseModel):
    """`kind` picks a template: instructions, tool_grants and max_steps left
    out of the request take the type's defaults. Fixed after creation."""

    kind: AgentKind = DEFAULT_KIND
    name: str = Field(min_length=1, max_length=80)
    agency: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=8000)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=50)
    tool_grants: list[str] = Field(default_factory=list)
    agent_id: str | None = Field(default=None, pattern=AGENT_ID_PATTERN)
    # Mailing list; see PUT /agents/{id}/recipients.
    recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS * 2)


class EmailSettingsUpdate(BaseModel):
    """Replaces the agent's sender settings. Empty strings clear a field; an empty
    from_email falls back to HOMEFRONT_EMAIL_FROM."""

    from_email: str = Field(default="", max_length=254)
    from_name: str = Field(default="", max_length=MAX_FROM_NAME * 2)
    reply_to: str = Field(default="", max_length=254)
    signature: str = Field(default="", max_length=MAX_SIGNATURE * 2)


class TestEmailRequest(BaseModel):
    to: str = Field(min_length=3, max_length=254)


class RecipientsUpdate(BaseModel):
    """Replaces the whole mailing list. Addresses are trimmed, lower-cased and de-duplicated."""

    emails: list[str] = Field(max_length=MAX_RECIPIENTS * 2)


class UpdateAgentRequest(BaseModel):
    """Partial update of an agent's definition. Use the /grants routes for tools."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    agency: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=8000)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    max_steps: int | None = Field(default=None, ge=1, le=50)
    active: bool | None = None


def _agent_view(agent: AgentRecord) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "kind": agent.kind,
        "name": agent.name,
        "agency": agent.agency,
        "instructions": agent.instructions,
        "model": agent.model,
        "max_steps": agent.max_steps,
        "owner_id": agent.owner_id,
        "tool_grants": sorted(agent.tool_grants),
        "recipients": list(agent.recipients),
        "email_settings": agent.email_settings.view(),
        "token_version": agent.token_version,
        "active": agent.active,
    }


def _recipients_error(exc: InvalidRecipientsError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "invalid_recipients", "message": str(exc), "invalid": exc.invalid},
    )


# Tools whose audience is limited by the agent's mailing list.
EMAIL_SEND = "email.send"
EMAIL_SEND_LIST = "email.send_list"
EMAIL_TOOLS = (EMAIL_SEND, EMAIL_SEND_LIST)
# Tools whose handler needs to know the calling agent (gateway-injected).
CALLER_CONTEXT_TOOLS = frozenset({"report.save"})


def _sender_preview(agent: AgentRecord) -> dict[str, Any]:
    """What a reviewer needs to see about who an email comes from."""
    sender = resolve_sender(agent.email_settings.view())
    return {
        "from": sender["from_header"] or None,
        "from_source": sender["source"],
        "reply_to": sender["reply_to"] or None,
        "signature": sender["signature"] or None,
        "simulated": not sender["from_email"],
    }


def _recipient_problem(agent: AgentRecord, tool_name: str, arguments: dict[str, Any]) -> str | None:
    """Why this call would email someone outside the agent's list, or None if it's fine."""
    if tool_name == EMAIL_SEND:
        to = normalize_email(str(arguments.get("to") or ""))
        if to not in agent.recipients:
            return f"{to or 'the recipient'} isn't on this agent's mailing list"
    elif tool_name == EMAIL_SEND_LIST and not agent.recipients:
        return "this agent's mailing list is empty"
    return None


@dataclass(frozen=True)
class Actor:
    """Who is calling an agent-management route.

    user_id None = anonymous operator (standalone demo console). Agents with
    no owner are shared demo agents any caller may manage; owned agents are
    visible only to their owner.
    """

    user_id: str | None = None

    def can_access(self, agent: AgentRecord) -> bool:
        return agent.owner_id is None or agent.owner_id == self.user_id

    @property
    def label(self) -> str:
        """How this caller appears in the activity log."""
        return f"user:{self.user_id}" if self.user_id else "anonymous"


ActorResolver = Callable[[str | None], Actor]


def _anonymous(_authorization: str | None) -> Actor:
    return Actor()


# The host app replaces this to authenticate users (see Backend/app/agent_iam.py).
# Standalone, every caller is anonymous and sees only ownerless agents.
_actor_resolver: ActorResolver = _anonymous


def set_actor_resolver(resolver: ActorResolver) -> None:
    global _actor_resolver
    _actor_resolver = resolver


def get_actor(authorization: str | None = Header(default=None)) -> Actor:
    return _actor_resolver(authorization)


def _accessible_agent(agent_id: str, actor: Actor) -> AgentRecord:
    agent = store.get_agent(agent_id)
    # 404, not 403, so callers can't probe which agent ids exist.
    if agent is None or not actor.can_access(agent):
        raise HTTPException(status_code=404, detail="Unknown agent")
    return agent


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization.removeprefix("Bearer ").strip()


def require_agent(authorization: str | None = Header(default=None)):
    return resolve_agent_token(_bearer(authorization))


def resolve_agent_token(token: str) -> tuple[AgentRecord, Any]:
    """Resolve an agent workload token or raise 401 (ATLAS-tagged when revoked)."""
    resolved = store.resolve_token(token)
    if not resolved:
        # Distinguish revoked/stale tokens for the demo narrative.
        tok = store.tokens.get(token)
        if tok:
            agent = store.get_agent(tok.agent_id)
            if agent and tok.token_version != agent.token_version:
                atlas = enricher.annotate(
                    AnnotateRequest(
                        event=AnnotateEvent.deny,
                        tool=None,
                        agent_id=tok.agent_id,
                        reason="token_version invalidated by grant revoke",
                    )
                )
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": "token_revoked",
                        "message": "Token invalidated mid-task after grant change",
                        "atlas": atlas.model_dump(),
                    },
                )
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return resolved


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "atlas_version": enricher.version,
        "agents": store.agent_count(),
    }


@app.get("/tools")
def tools_catalog() -> list[dict[str, Any]]:
    """Public tool catalog with ATLAS risk labels (no secrets)."""
    return [tool_public_view(t) for t in list_tools()]


@app.get("/agent-types")
def agent_types_catalog() -> list[dict[str, Any]]:
    """Public catalog of agent types: templates and tool ceilings."""
    return [agent_type_view(t) for t in AGENT_TYPES.values()]


def _kind_denial(kind: str, tools: list[str]) -> dict[str, Any]:
    label = get_agent_type(kind).label
    return {
        "error": "tool_not_allowed_for_kind",
        "message": f"{label} agents can't hold {', '.join(tools)} (AML.M0028 least privilege).",
        "kind": kind,
        "tools": tools,
    }


def _enforce_kind_ceiling(agent: AgentRecord, tools: list[str], actor: Actor) -> None:
    """Raise 403 (and audit a deny) if any of `tools` is outside the agent's type."""
    blocked = disallowed_tools(agent.kind, tools)
    if not blocked:
        return
    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.deny,
            tool=blocked[0],
            agent_id=agent.agent_id,
            reason=f"tool outside {agent.kind} agent ceiling",
        )
    )
    event = store.append_audit(
        {
            "type": "grant_denied",
            "decision": "deny",
            "agent_id": agent.agent_id,
            "actor": actor.label,
            "tool": blocked[0],
            "tools": blocked,
            "kind": agent.kind,
            "atlas": atlas.model_dump(),
        }
    )
    raise HTTPException(
        status_code=403,
        detail={**_kind_denial(agent.kind, blocked), "atlas": atlas.model_dump(), "audit_id": event["id"]},
    )


@app.get("/agents")
def agents(actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    return [_agent_view(a) for a in store.list_agents() if actor.can_access(a)]


@app.post("/agents", status_code=201)
def create_agent(
    body: CreateAgentRequest, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    agent_type = get_agent_type(body.kind)
    sent = body.model_fields_set
    tool_grants = body.tool_grants if "tool_grants" in sent else sorted(agent_type.default_tools)
    instructions = (
        body.instructions if "instructions" in sent else agent_type.default_instructions
    )
    max_steps = body.max_steps if "max_steps" in sent else agent_type.default_max_steps

    unknown = [t for t in tool_grants if get_tool(t) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools: {unknown}")
    blocked = disallowed_tools(body.kind, tool_grants)
    if blocked:
        # No agent exists yet, so nothing to audit against.
        raise HTTPException(status_code=403, detail=_kind_denial(body.kind, blocked))
    try:
        agent = store.create_agent(
            body.name,
            body.agency,
            tool_grants,
            body.agent_id,
            instructions=instructions,
            model=body.model,
            max_steps=max_steps,
            owner_id=actor.user_id,
            kind=body.kind,
            recipients=body.recipients,
        )
    except InvalidRecipientsError as exc:
        raise _recipients_error(exc)
    except DuplicateAgentError:
        raise HTTPException(status_code=409, detail=f"Agent {body.agent_id} already exists")
    store.append_audit(
        {
            "type": "agent_created",
            "decision": "info",
            "agent_id": agent.agent_id,
            "actor": actor.label,
            "kind": agent.kind,
            "tool_grants": sorted(agent.tool_grants),
            "detail": f"Created {agent.kind} agent \"{agent.name}\"",
        }
    )
    return _agent_view(agent)


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return _agent_view(_accessible_agent(agent_id, actor))


@app.patch("/agents/{agent_id}")
def update_agent(
    agent_id: str, body: UpdateAgentRequest, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    _accessible_agent(agent_id, actor)
    # exclude_unset: only fields the caller sent. "model": null resets to the
    # runner default; null for any other field is ignored.
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k == "model"}
    try:
        agent = store.update_agent(agent_id, **changes)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown agent")
    if changes:
        store.append_audit(
            {
                "type": "agent_updated",
                "decision": "info",
                "agent_id": agent_id,
                "actor": actor.label,
                "fields": sorted(changes),
                "detail": f"Changed {', '.join(sorted(changes))}",
            }
        )
    return _agent_view(agent)


@app.put("/agents/{agent_id}/recipients")
def replace_recipients(
    agent_id: str, body: RecipientsUpdate, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    before = set(_accessible_agent(agent_id, actor).recipients)
    try:
        agent = store.set_recipients(agent_id, body.emails)
    except InvalidRecipientsError as exc:
        raise _recipients_error(exc)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown agent")
    after = set(agent.recipients)
    added, removed = sorted(after - before), sorted(before - after)
    if added or removed:
        store.append_audit(
            {
                "type": "recipients_updated",
                "decision": "info",
                "agent_id": agent_id,
                "actor": actor.label,
                "added": added,
                "removed": removed,
                "detail": f"Mailing list: {len(after)} addresses (+{len(added)}, -{len(removed)})",
            }
        )
    return _agent_view(agent)


@app.put("/agents/{agent_id}/email-settings")
def replace_email_settings(
    agent_id: str, body: EmailSettingsUpdate, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    before = _accessible_agent(agent_id, actor).email_settings.view()
    try:
        settings = normalize_email_settings(**body.model_dump())
        agent = store.set_email_settings(agent_id, settings)
    except InvalidEmailSettingsError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_email_settings", "field": exc.field, "message": str(exc)},
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown agent")
    after = agent.email_settings.view()
    changed = sorted(k for k in after if after[k] != before.get(k))
    if changed:
        store.append_audit(
            {
                "type": "email_settings_updated",
                "decision": "info",
                "agent_id": agent_id,
                "actor": actor.label,
                "fields": changed,
                "detail": f"Email setup: changed {', '.join(changed)}",
            }
        )
    return _agent_view(agent)


@app.get("/agents/{agent_id}/email-settings/verification")
def email_settings_verification(agent_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """Is the effective sender verified in SES? verified=null when it can't be checked."""
    agent = _accessible_agent(agent_id, actor)
    sender = resolve_sender(agent.email_settings.view())
    if not sender["from_email"]:
        return {"from_email": None, "source": None, "verified": None, "reason": "No sender email is set."}
    status = ses_identity_status(sender["from_email"])
    return {"from_email": sender["from_email"], "source": sender["source"], **status}


def _ses_http_error(exc: email_admin.SesActionError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"error": exc.code, "message": str(exc)})


@app.get("/email/account")
def email_account(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """SES sandbox status and sending quota (cached ~60s). Nulls + reason when it can't be read."""
    return email_admin.account_status()


@app.get("/email/permissions")
def email_permissions(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """Whether the server's AWS role may send email / request verification (IAM simulation)."""
    return email_admin.permissions()


@app.post("/agents/{agent_id}/email-settings/verify-sender")
def verify_sender(agent_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """Ask SES to email the sender address a verification link (CreateEmailIdentity)."""
    agent = _accessible_agent(agent_id, actor)
    sender = resolve_sender(agent.email_settings.view())
    if not sender["from_email"]:
        raise HTTPException(
            status_code=400, detail={"error": "no_sender", "message": "Set a sender email first."}
        )
    try:
        out = email_admin.request_verification(sender["from_email"])
    except email_admin.SesActionError as exc:
        raise _ses_http_error(exc)
    store.append_audit(
        {
            "type": "sender_verification_requested",
            "decision": "info",
            "agent_id": agent_id,
            "actor": actor.label,
            "detail": out["message"],
        }
    )
    return out


@app.post("/agents/{agent_id}/email-settings/test")
def send_test_email(
    agent_id: str, body: TestEmailRequest, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """A person-triggered test send through SES: proves the sender, permissions and
    sandbox status. `to` must be on the mailing list or be the sender itself."""
    agent = _accessible_agent(agent_id, actor)
    settings = agent.email_settings.view()
    sender = email_handlers.resolve_sender(settings)
    to = normalize_email(body.to)
    if not sender["from_email"]:
        raise HTTPException(
            status_code=400, detail={"error": "no_sender", "message": "Set a sender email first."}
        )
    if to not in agent.recipients and to != normalize_email(sender["from_email"]):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "recipient_not_allowed",
                "message": f"{to} isn't on this agent's mailing list or its sender address.",
            },
        )
    subject = f"Test email from {agent.name}"
    text = email_handlers.compose_body(
        f"This is a test email from the HomeFront agent \"{agent.name}\" ({agent.agency}).\n"
        f"If you received it, {sender['from_email']} can send through Amazon SES.",
        sender["signature"],
    )
    base = {"to": to, "from": sender["from_header"], "subject": subject}
    try:
        message_id = email_handlers._ses_send(email_handlers._ses_client(quick=True), sender, to, subject, text)
        out = {**base, "ok": True, "message_id": message_id, "error": None, "error_code": None}
    except Exception as exc:
        out = {
            **base,
            "ok": False,
            "message_id": None,
            "error_code": email_admin.error_code(exc),
            "error": email_admin.error_text(exc)
            if not email_admin._no_credentials(exc)
            else email_admin._unavailable_reason(exc),
        }
    store.append_audit(
        {
            "type": "email_test",
            "decision": "allow" if out["ok"] else "deny",
            "agent_id": agent_id,
            "actor": actor.label,
            "tool": "email.send",
            "detail": f"Test email to {to}: "
            + (f"sent ({out['message_id']})" if out["ok"] else out["error"]),
        }
    )
    return out


@app.get("/agents/{agent_id}/recipients/verification")
def recipients_verification(agent_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """Per-recipient SES verification status (matters only in the sandbox)."""
    agent = _accessible_agent(agent_id, actor)
    result = email_admin.address_statuses(agent.recipients)
    statuses = result["statuses"]
    return {
        "available": result["available"],
        "reason": result["reason"],
        "total": len(agent.recipients),
        "verified": sum(1 for s in statuses.values() if s == "verified"),
        "recipients": [{"address": a, "status": statuses.get(a)} for a in agent.recipients],
    }


@app.post("/agents/{agent_id}/recipients/verify")
def verify_recipients(agent_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """Ask SES to send verification links to unverified recipients, up to
    email_admin.CREATE_BATCH per call (SES allows ~1 such request per second).
    Call again while `remaining` > 0."""
    agent = _accessible_agent(agent_id, actor)
    if not agent.recipients:
        raise HTTPException(
            status_code=400, detail={"error": "empty_list", "message": "The mailing list is empty."}
        )
    try:
        out = email_admin.request_verifications(list(agent.recipients))
    except email_admin.SesActionError as exc:
        raise _ses_http_error(exc)
    if out["requested"] or out["failed"]:
        store.append_audit(
            {
                "type": "recipients_verification_requested",
                "decision": "info",
                "agent_id": agent_id,
                "actor": actor.label,
                "detail": f"Verification emails requested for {len(out['requested'])} recipient(s)"
                + (f", {len(out['failed'])} failed" if out["failed"] else ""),
            }
        )
    return out


@app.post("/auth/token", response_model=AuthResponse)
def authenticate(body: AuthRequest, actor: Actor = Depends(get_actor)) -> AuthResponse:
    agent = _accessible_agent(body.agent_id, actor)
    if not agent.active:
        raise HTTPException(status_code=403, detail="Agent is inactive")
    tok = store.issue_token(agent.agent_id, body.ttl_seconds)
    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.allow,
            agent_id=agent.agent_id,
            reason="workload identity issued",
        )
    )
    # Auth itself is not a tool call; still log under AML.M0024 telemetry.
    store.append_audit(
        {
            "type": "authenticate",
            "decision": "allow",
            "agent_id": agent.agent_id,
            "trace_id": tok.session_id,
            "actor": actor.label,
            "tool": None,
            "atlas": atlas.model_dump(),
        }
    )
    return AuthResponse(
        access_token=tok.token,
        agent_id=agent.agent_id,
        agency=agent.agency,
        tool_grants=sorted(agent.tool_grants),
        token_version=tok.token_version,
        session_id=tok.session_id,
        atlas={
            "mitigation_ids": ["AML.M0028", "AML.M0024"],
            "narrative": "Agent authenticated; tool grants enforced per AML.M0028.",
        },
    )


@app.get("/auth/me")
def me(resolved=Depends(require_agent)) -> dict[str, Any]:
    agent, tok = resolved
    return {**_agent_view(agent), "token_version": tok.token_version}


@app.put("/agents/{agent_id}/grants")
def replace_grants(
    agent_id: str, body: GrantUpdate, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    current = _accessible_agent(agent_id, actor)
    unknown = [t for t in body.tools if get_tool(t) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools: {unknown}")
    _enforce_kind_ceiling(current, body.tools, actor)
    agent = store.set_grants(agent_id, body.tools)
    atlas = enricher.annotate(
        AnnotateRequest(event=AnnotateEvent.revoke, agent_id=agent_id, reason="grants replaced")
    )
    store.append_audit(
        {
            "type": "grants_replaced",
            "actor": actor.label,
            "decision": "revoke",
            "agent_id": agent_id,
            "tool": None,
            "tool_grants": sorted(agent.tool_grants),
            "atlas": atlas.model_dump(),
        }
    )
    return {
        "agent_id": agent.agent_id,
        "tool_grants": sorted(agent.tool_grants),
        "token_version": agent.token_version,
        "atlas": atlas.model_dump(),
    }


@app.post("/agents/{agent_id}/grants/{tool_name}/revoke")
def revoke_tool(
    agent_id: str, tool_name: str, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    _accessible_agent(agent_id, actor)
    if get_tool(tool_name) is None:
        raise HTTPException(status_code=404, detail="Unknown tool")
    agent = store.revoke_tool(agent_id, tool_name)
    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.revoke,
            tool=tool_name,
            agent_id=agent_id,
            reason=f"revoked {tool_name}",
        )
    )
    store.append_audit(
        {
            "type": "grant_revoked",
            "actor": actor.label,
            "decision": "revoke",
            "agent_id": agent_id,
            "tool": tool_name,
            "tool_grants": sorted(agent.tool_grants),
            "atlas": atlas.model_dump(),
        }
    )
    return {
        "agent_id": agent.agent_id,
        "revoked": tool_name,
        "tool_grants": sorted(agent.tool_grants),
        "token_version": agent.token_version,
        "atlas": atlas.model_dump(),
        "message": (
            f"Revoked {tool_name} mid-task; same token remains valid for other grants, "
            "but this tool is now blocked (AML.T0053 / AML.M0028)."
        ),
    }


@app.post("/agents/{agent_id}/grants/{tool_name}")
def grant_tool(
    agent_id: str, tool_name: str, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    current = _accessible_agent(agent_id, actor)
    if get_tool(tool_name) is None:
        raise HTTPException(status_code=404, detail="Unknown tool")
    _enforce_kind_ceiling(current, [tool_name], actor)
    agent = store.grant_tool(agent_id, tool_name)
    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.allow,
            tool=tool_name,
            agent_id=agent_id,
            reason=f"granted {tool_name}",
        )
    )
    store.append_audit(
        {
            "type": "grant_added",
            "actor": actor.label,
            "decision": "allow",
            "agent_id": agent_id,
            "tool": tool_name,
            "tool_grants": sorted(agent.tool_grants),
            "atlas": atlas.model_dump(),
        }
    )
    return {
        "agent_id": agent.agent_id,
        "granted": tool_name,
        "tool_grants": sorted(agent.tool_grants),
        "token_version": agent.token_version,
        "atlas": atlas.model_dump(),
    }


@app.post("/mcp/call")
def mcp_call(body: ToolCallRequest, resolved=Depends(require_agent)) -> dict[str, Any]:
    agent, tok = resolved
    return call_tool(agent, tok, body.tool, body.arguments)


def gateway_tool_call(
    token: str, tool_name: str, arguments: dict[str, Any], run_id: str | None = None
) -> dict[str, Any]:
    """The runner's only way to use a tool: same token check and grant check as
    POST /mcp/call, re-evaluated on every call so mid-run revokes apply."""
    agent, tok = resolve_agent_token(token)
    return call_tool(agent, tok, tool_name, arguments, run_id=run_id)


def call_tool(
    agent: AgentRecord,
    tok: Any,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Authorize, execute and audit one tool call. Raises HTTPException on deny."""
    tool = get_tool(tool_name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool {tool_name}")

    # Secret probe: refuse any attempt to request credentials via args.
    if any(k.lower() in {"api_key", "x-api-key", "secret", "token"} for k in arguments):
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.secret_probe,
                tool=tool_name,
                agent_id=agent.agent_id,
            )
        )
        event = store.append_audit(
            {
                "type": "tool_call",
                "run_id": run_id,
                "trace_id": run_id or getattr(tok, "session_id", None),
                "actor": f"agent:{agent.agent_id}",
                "decision": "deny",
                "agent_id": agent.agent_id,
                "tool": tool_name,
                "args_hash": _hash_args(arguments),
                "atlas": atlas.model_dump(),
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "secret_probe_blocked",
                "message": "Credentials are not exposed to agents (AML.M0026).",
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    # Defense in depth: the grant routes already enforce the type's ceiling,
    # but a grant written straight to the table must not get past it either.
    if disallowed_tools(agent.kind, [tool_name]):
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=tool_name,
                agent_id=agent.agent_id,
                reason=f"tool outside {agent.kind} agent ceiling",
            )
        )
        event = store.append_audit(
            {
                "type": "tool_call",
                "run_id": run_id,
                "trace_id": run_id or getattr(tok, "session_id", None),
                "actor": f"agent:{agent.agent_id}",
                "decision": "deny",
                "agent_id": agent.agent_id,
                "tool": tool_name,
                "args_hash": _hash_args(arguments),
                "atlas": atlas.model_dump(),
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                **_kind_denial(agent.kind, [tool_name]),
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    if tool_name not in agent.tool_grants:
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=tool_name,
                agent_id=agent.agent_id,
                reason="tool not in grant set",
            )
        )
        event = store.append_audit(
            {
                "type": "tool_call",
                "run_id": run_id,
                "trace_id": run_id or getattr(tok, "session_id", None),
                "actor": f"agent:{agent.agent_id}",
                "decision": "deny",
                "agent_id": agent.agent_id,
                "tool": tool_name,
                "args_hash": _hash_args(arguments),
                "atlas": atlas.model_dump(),
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tool_not_granted",
                "message": f"Blocked {tool_name}: not in agent grants (AML.T0053 / AML.M0028).",
                "technique_ids": atlas.technique_ids,
                "mitigation_ids": atlas.mitigation_ids,
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    # Outbound email only reaches the agent's mailing list. Checked before
    # queuing so an injected recipient never even reaches a reviewer.
    problem = _recipient_problem(agent, tool_name, arguments)
    if problem:
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=tool_name,
                agent_id=agent.agent_id,
                reason="recipient not on agent mailing list",
            )
        )
        event = store.append_audit(
            {
                "type": "tool_call",
                "run_id": run_id,
                "trace_id": run_id or getattr(tok, "session_id", None),
                "actor": f"agent:{agent.agent_id}",
                "decision": "deny",
                "agent_id": agent.agent_id,
                "tool": tool_name,
                "args_hash": _hash_args(arguments),
                "atlas": atlas.model_dump(),
                "detail": f"Blocked: {problem}",
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "recipient_not_allowed",
                "message": f"Blocked {tool_name}: {problem} (AML.M0028).",
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    # High-risk outbound tools wait for a person (AML.M0029).
    if tool.requires_hitl:
        return _request_approval(agent, tok, tool_name, arguments, run_id)

    # Annotate untrusted input tools before execution.
    if tool.untrusted_input:
        untrusted = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.untrusted_input,
                tool=tool_name,
                agent_id=agent.agent_id,
            )
        )
    else:
        untrusted = None

    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.allow,
            tool=tool_name,
            agent_id=agent.agent_id,
        )
    )
    run_args = dict(arguments)
    if tool_name in CALLER_CONTEXT_TOOLS:
        # Who is calling comes from the workload token, never from arguments.
        run_args.update(
            agent_id=agent.agent_id,
            trace_id=run_id or getattr(tok, "session_id", None),
            run_id=run_id,
        )
    result = _run_handler(tool, tool_name, run_args)

    event = store.append_audit(
        {
            "type": "tool_call",
            "run_id": run_id,
            "trace_id": run_id or getattr(tok, "session_id", None),
            "actor": f"agent:{agent.agent_id}",
            "decision": "allow",
            "agent_id": agent.agent_id,
            "tool": tool_name,
            "args_hash": _hash_args(arguments),
            "atlas": atlas.model_dump(),
            "untrusted_input_atlas": untrusted.model_dump() if untrusted else None,
        }
    )
    return {
        "decision": "allow",
        "tool": tool_name,
        "agent_id": agent.agent_id,
        "token_version": tok.token_version,
        "result": result,
        "atlas": atlas.model_dump(),
        "untrusted_input_atlas": untrusted.model_dump() if untrusted else None,
        "audit_id": event["id"],
    }


def _run_handler(tool: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = invoke_tool(tool_name, arguments)
    # Hard guarantee: never leak gateway-held api keys.
    if tool.holds_secrets:
        result = {**result, "credentials_exposed": False}
        result.pop("api_key", None)
        result.pop("X-Api-Key", None)
    return result


HITL_ATLAS = {
    "mitigation_ids": ["AML.M0029"],
    "narrative": "High-risk agent action held for human approval (AML.M0029 Human In-the-Loop).",
}


def _request_approval(
    agent: AgentRecord, tok: Any, tool_name: str, arguments: dict[str, Any], run_id: str | None
) -> dict[str, Any]:
    trace_id = run_id or getattr(tok, "session_id", None)
    rec = store.approvals.insert(
        ApprovalRecord(
            agent_id=agent.agent_id,
            tool=tool_name,
            arguments=dict(arguments),
            trace_id=trace_id,
            run_id=run_id,
        )
    )
    event = store.append_audit(
        {
            "type": "approval_requested",
            "decision": "pending",
            "agent_id": agent.agent_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "actor": f"agent:{agent.agent_id}",
            "tool": tool_name,
            "approval_id": rec.approval_id,
            "args_hash": _hash_args(arguments),
            "atlas": HITL_ATLAS,
            "detail": f"Waiting for a person to approve {tool_name}",
        }
    )
    return {
        "decision": "pending_approval",
        "tool": tool_name,
        "agent_id": agent.agent_id,
        "token_version": tok.token_version,
        "approval_id": rec.approval_id,
        # What the agent sees as the tool result.
        "result": {
            "status": "pending_approval",
            "approval_id": rec.approval_id,
            "message": (
                f"{tool_name} was queued for human approval and has NOT been performed yet. "
                "Don't call it again for the same content; mention it in your summary."
            ),
        },
        "atlas": HITL_ATLAS,
        "audit_id": event["id"],
    }


class ApprovalDecision(BaseModel):
    # Approve only: replacement arguments (a reviewer's edits). Omit to use the agent's.
    arguments: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=1000)


def _approval_view(rec: ApprovalRecord, agent: AgentRecord | None) -> dict[str, Any]:
    view = rec.view()
    tool = get_tool(rec.tool)
    view["agent"] = (
        {"agent_id": agent.agent_id, "name": agent.name, "agency": agent.agency, "kind": agent.kind}
        if agent
        else None
    )
    view["tool_description"] = tool.description if tool else None
    view["input_schema"] = tool.input_schema if tool else None
    # Who a list send goes to if approved now (the list as it is today).
    view["recipients"] = list(agent.recipients) if agent and rec.tool == EMAIL_SEND_LIST else None
    # The sender and signature the gateway adds if approved now.
    view["sender"] = _sender_preview(agent) if agent and rec.tool in EMAIL_TOOLS else None
    view["delivery"] = _delivery_check(rec, agent) if agent and rec.status == "pending" else None
    return view


def _delivery_check(rec: ApprovalRecord, agent: AgentRecord) -> dict[str, Any] | None:
    """In the SES sandbox, which recipients SES would reject. A warning, never a block."""
    if rec.tool not in EMAIL_TOOLS:
        return None
    if rec.tool == EMAIL_SEND:
        recipients = [normalize_email(str(rec.arguments.get("to") or ""))]
    else:
        recipients = list(agent.recipients)
    if not recipients or _sender_preview(agent)["simulated"]:
        return None
    account = email_admin.account_status()
    if account["sandbox"] is not True:
        return {"sandbox": account["sandbox"], "unverified": [], "reason": account["reason"]}
    missing = email_admin.unverified(recipients)
    return {
        "sandbox": True,
        "unverified": missing,  # None = couldn't tell
        "recipient_count": len(recipients),
        "reason": "SES sandbox: unverified recipients will be rejected by SES.",
    }


def _accessible_approval(approval_id: str, actor: Actor) -> tuple[ApprovalRecord, AgentRecord]:
    rec = store.approvals.get(approval_id)
    agent = store.get_agent(rec.agent_id) if rec else None
    # 404 for other users' approvals, like agents.
    if rec is None or agent is None or not actor.can_access(agent):
        raise HTTPException(status_code=404, detail="Unknown approval")
    return rec, agent


def _validate_arguments(tool_name: str, arguments: dict[str, Any]) -> None:
    """Edited arguments must still match the tool's schema (all strings, no extras)."""
    tool = get_tool(tool_name)
    props = (tool.input_schema or {}).get("properties", {}) if tool else {}
    unknown = sorted(set(arguments) - set(props))
    missing = sorted(set(props) - set(arguments))
    wrong = sorted(k for k, v in arguments.items() if k in props and not isinstance(v, str))
    if unknown or missing or wrong:
        parts = []
        if unknown:
            parts.append(f"unknown fields {unknown}")
        if missing:
            parts.append(f"missing fields {missing}")
        if wrong:
            parts.append(f"non-text fields {wrong}")
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_arguments", "message": f"Invalid {tool_name} arguments: {'; '.join(parts)}"},
        )


def _decision_conflict(status: str) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error": "already_decided", "status": status, "message": f"This request is already {status}."},
    )


def _expire_if_stale(rec: ApprovalRecord, actor: Actor) -> None:
    if not rec.is_expired():
        return
    try:
        store.approvals.transition(rec.approval_id, "pending", status="expired")
        store.append_audit(
            {
                "type": "approval_expired",
                "decision": "deny",
                "agent_id": rec.agent_id,
                "run_id": rec.run_id,
                "trace_id": rec.trace_id,
                "actor": actor.label,
                "tool": rec.tool,
                "approval_id": rec.approval_id,
                "detail": "Not decided within 24 hours",
            }
        )
    except ApprovalConflict:
        pass
    raise _decision_conflict("expired")


@app.get("/approvals")
def list_approvals(
    status: str | None = None, actor: Actor = Depends(get_actor)
) -> list[dict[str, Any]]:
    """Approval requests for the caller's agents, newest first. ?status=pending etc."""
    agents_by_id = {a.agent_id: a for a in store.list_agents() if actor.can_access(a)}
    out = []
    for rec in store.approvals.list():
        agent = agents_by_id.get(rec.agent_id)
        if agent is None:
            continue
        view = _approval_view(rec, agent)
        if status is None or view["status"] == status:
            out.append(view)
    out.sort(key=lambda v: v["requested_at"], reverse=True)
    return out[:200]


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    rec, agent = _accessible_approval(approval_id, actor)
    return _approval_view(rec, agent)


@app.post("/approvals/{approval_id}/approve")
def approve(
    approval_id: str, body: ApprovalDecision, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    """Approve (optionally with edited arguments) and execute the tool once."""
    rec, agent = _accessible_approval(approval_id, actor)
    _expire_if_stale(rec, actor)
    if rec.status != "pending":
        raise _decision_conflict(rec.status)

    arguments = dict(body.arguments) if body.arguments is not None else dict(rec.arguments)
    _validate_arguments(rec.tool, arguments)
    if any(k.lower() in {"api_key", "x-api-key", "secret", "token"} for k in arguments):
        raise HTTPException(status_code=400, detail="Credentials can't be passed as tool arguments")

    # Re-check authorization at approval time: a revoke after the request wins.
    blocked_reason = None
    if not agent.active:
        blocked_reason = "the agent is inactive"
    elif rec.tool not in agent.tool_grants:
        blocked_reason = f"{rec.tool} was revoked"
    elif disallowed_tools(agent.kind, [rec.tool]):
        blocked_reason = f"{rec.tool} isn't allowed for {agent.kind} agents"
    else:
        # Reviewer edits and list changes since the request both count.
        blocked_reason = _recipient_problem(agent, rec.tool, arguments)
    if blocked_reason:
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=rec.tool,
                agent_id=agent.agent_id,
                reason=f"approval blocked: {blocked_reason}",
            )
        )
        event = store.append_audit(
            {
                "type": "approval_blocked",
                "decision": "deny",
                "agent_id": agent.agent_id,
                "run_id": rec.run_id,
                "trace_id": rec.trace_id,
                "actor": actor.label,
                "tool": rec.tool,
                "approval_id": rec.approval_id,
                "atlas": atlas.model_dump(),
                "detail": f"Can't approve: {blocked_reason}",
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "no_longer_authorized",
                "message": f"Can't approve: {blocked_reason}. The request stays pending.",
                "audit_id": event["id"],
            },
        )

    edited = arguments != rec.arguments
    try:
        claimed = store.approvals.transition(
            approval_id,
            "pending",
            status="approved",
            decided_by=actor.label,
            decided_at=time.time(),
            note=body.note,
            arguments=arguments,
            original_arguments=dict(rec.arguments) if edited else None,
        )
    except ApprovalConflict as exc:
        raise _decision_conflict(str(exc))

    base = {
        "agent_id": agent.agent_id,
        "run_id": rec.run_id,
        "trace_id": rec.trace_id,
        "tool": rec.tool,
        "approval_id": rec.approval_id,
    }
    store.append_audit(
        {
            **base,
            "type": "approval_approved",
            "decision": "allow",
            "actor": actor.label,
            "edited": edited,
            "atlas": HITL_ATLAS,
            "detail": ("Approved with edits" if edited else "Approved") + (f": {body.note}" if body.note else ""),
        }
    )

    tool = get_tool(rec.tool)
    run_args = dict(arguments)
    if rec.tool in EMAIL_TOOLS:
        # Sender and audience come from the agent's record, never from the
        # agent's or reviewer's arguments.
        run_args["sender"] = agent.email_settings.view()
    if rec.tool == EMAIL_SEND_LIST:
        run_args["recipients"] = list(agent.recipients)
    try:
        result = _run_handler(tool, rec.tool, run_args)
    except Exception as exc:  # record the failure; the approval is spent either way
        failed = store.approvals.transition(
            approval_id, "approved", status="failed", error=f"{type(exc).__name__}: {exc}"
        )
        store.append_audit(
            {
                **base,
                "type": "tool_call",
                "decision": "deny",
                "actor": f"agent:{agent.agent_id}",
                "approved_by": actor.label,
                "detail": f"Approved action failed: {type(exc).__name__}",
            }
        )
        raise HTTPException(
            status_code=502, detail={"error": "tool_failed", "approval": _approval_view(failed, agent)}
        )

    done = store.approvals.transition(approval_id, "approved", status="executed", result=result)
    atlas = enricher.annotate(
        AnnotateRequest(event=AnnotateEvent.allow, tool=rec.tool, agent_id=agent.agent_id)
    )
    store.append_audit(
        {
            **base,
            "type": "tool_call",
            "decision": "allow",
            "actor": f"agent:{agent.agent_id}",
            "approved_by": actor.label,
            "args_hash": _hash_args(arguments),
            "atlas": atlas.model_dump(),
            "detail": f"Executed after approval by {actor.label}",
        }
    )
    return _approval_view(done, agent)


@app.post("/approvals/{approval_id}/reject")
def reject(
    approval_id: str, body: ApprovalDecision, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    rec, agent = _accessible_approval(approval_id, actor)
    _expire_if_stale(rec, actor)
    try:
        done = store.approvals.transition(
            approval_id,
            "pending",
            status="rejected",
            decided_by=actor.label,
            decided_at=time.time(),
            note=body.note,
        )
    except ApprovalConflict as exc:
        raise _decision_conflict(str(exc))
    store.append_audit(
        {
            "type": "approval_rejected",
            "decision": "deny",
            "agent_id": agent.agent_id,
            "run_id": rec.run_id,
            "trace_id": rec.trace_id,
            "actor": actor.label,
            "tool": rec.tool,
            "approval_id": rec.approval_id,
            "atlas": HITL_ATLAS,
            "detail": "Rejected" + (f": {body.note}" if body.note else ""),
        }
    )
    return _approval_view(done, agent)


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)


def configure_runner(new_runner: AgentRunner | None) -> None:
    global runner
    runner = new_runner


def _issue_run_token(agent_id: str, run_id: str | None = None) -> str:
    tok = store.issue_token(agent_id, ttl_seconds=3600, session_id=run_id)
    store.append_audit(
        {
            "type": "authenticate",
            "decision": "allow",
            "agent_id": agent_id,
            "run_id": run_id,
            "trace_id": tok.session_id,
            "actor": f"agent:{agent_id}",
            "tool": None,
            "reason": "workload token issued for agent run",
        }
    )
    return tok.token


@app.post("/agents/{agent_id}/runs", status_code=202)
def start_run(
    agent_id: str,
    body: CreateRunRequest,
    background: BackgroundTasks,
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Start the agent on a task. Returns immediately; poll GET .../runs/{run_id}."""
    agent = _accessible_agent(agent_id, actor)
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail="Agent runner is not configured (set BEDROCK_REGION / AWS_REGION).",
        )
    if not agent.active:
        raise HTTPException(status_code=403, detail="Agent is inactive")
    run = runs.add(new_run(agent, body.task, runner.model_for(agent), actor.user_id))
    store.append_audit(
        {
            "type": "run_started",
            "decision": "info",
            "agent_id": agent.agent_id,
            "run_id": run.run_id,
            "trace_id": run.run_id,
            "actor": actor.label,
            "detail": body.task[:200],
        }
    )
    background.add_task(_execute_and_audit, runner, run, agent)
    return run.view()


def _execute_and_audit(active_runner: AgentRunner, run: Any, agent: AgentRecord) -> None:
    active_runner.execute(run, agent)
    store.append_audit(
        {
            "type": "run_finished",
            "decision": "info" if run.status == "completed" else "deny",
            "agent_id": agent.agent_id,
            "run_id": run.run_id,
            "trace_id": run.run_id,
            "actor": f"agent:{agent.agent_id}",
            "status": run.status,
            "model_turns": run.model_turns,
            "detail": run.error or f"Finished in {run.model_turns} model turn(s)",
        }
    )


@app.get("/agents/{agent_id}/runs")
def list_runs(agent_id: str, actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    _accessible_agent(agent_id, actor)
    return [r.view() for r in runs.for_agent(agent_id)]


@app.get("/agents/{agent_id}/runs/{run_id}")
def get_run(agent_id: str, run_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    _accessible_agent(agent_id, actor)
    run = runs.get(run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Unknown run")
    return run.view()


@app.get("/agents/{agent_id}/reports")
def list_reports(
    agent_id: str, limit: int = 50, actor: Actor = Depends(get_actor)
) -> list[dict[str, Any]]:
    """Reports this agent saved with report.save, newest first."""
    _accessible_agent(agent_id, actor)
    limit = max(1, min(limit, 200))
    return [r.view() for r in store.reports.list(agent_id, limit)]


@app.get("/audit")
def audit(
    limit: int = 100,
    agent_id: str | None = None,
    trace_id: str | None = None,
    decision: str | None = None,
    type: str | None = None,
    actor: Actor = Depends(get_actor),
) -> list[dict[str, Any]]:
    """Activity log for the caller's agents, newest first. Filter by agent,
    trace (a run id or token session id), decision or event type."""
    visible = {a.agent_id for a in store.list_agents() if actor.can_access(a)}
    if agent_id is not None:
        visible &= {agent_id}
    limit = max(0, min(limit, 500))
    return store.list_audit(
        visible, limit, trace_id=trace_id, decision=decision, type_=type
    )


@app.get("/status")
def storage_status() -> dict[str, Any]:
    """Where agents and the activity log are actually being stored right now."""
    return {
        "agent_store": store.agent_backend,
        "audit_store": store.audit_backend,
        "approval_store": store.approval_backend,
        "report_store": store.report_backend,
        "audit_write_errors": store.audit_write_errors,
        "last_audit_error": store.last_audit_error,
        "warnings": list(store.storage_warnings),
        "runner_configured": runner is not None,
    }


@app.get("/demo/matrix")
def demo_matrix() -> dict[str, Any]:
    """Compact Agentic AI technique strip for the demo sidebar."""
    ids = [
        "AML.T0051",
        "AML.T0051.001",
        "AML.T0053",
        "AML.T0084.001",
        "AML.T0086",
        "AML.T0098",
        "AML.T0101",
        "AML.T0110",
    ]
    mitigations = ["AML.M0024", "AML.M0026", "AML.M0028", "AML.M0029", "AML.M0030", "AML.M0033"]
    return {
        "atlas_version": enricher.version,
        "techniques": [
            enricher.get_technique(i) or {"id": i, "name": None} for i in ids
        ],
        "mitigations": [
            enricher.get_mitigation(i) or {"id": i, "name": None} for i in mitigations
        ],
    }


def _hash_args(args: dict[str, Any]) -> str:
    blob = repr(sorted(args.items())).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def create_app() -> FastAPI:
    return app
