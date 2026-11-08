"""Teammates' agent-management API (main's old gateway), restored behind our Cognito login.

Agents with per-tool grants, a mailing list and an SES sender; runs of Claude on
Bedrock; human approvals; the activity log. It is a separate path from the
mandate flow (`app.py`, `gateway.py`, `/mcp`): nothing here mints or accepts a
mandate token, and nothing in the mandate flow reads this store.

Every route is mounted by `Backend/app/agent_iam.py` behind our Cognito login:
GET routes need `require_staff`, every other method `require_admin`. The actor
resolver below is set there too; until it is, every call is refused (401).
"""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from mandate_contracts.ddb import StafferItem
from mandate_contracts.roles import TOOL_ACTIONS, mandate_tool_name, role_allows, role_label, role_reason
from pydantic import BaseModel, ConfigDict, Field

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
from mcp_tools import handlers as email_handlers  # noqa: E402
from mcp_tools.handlers import (  # noqa: E402
    host_matches_domains,
    invoke_tool,
    resolve_sender,
    ses_identity_status,
)
from mcp_tools.registry import TOOL_REGISTRY, get_tool, list_tools, tool_public_view  # noqa: E402
from mcp_tools.share_links import ShareLinkError, normalize_platform  # noqa: E402

from . import email_admin  # noqa: E402

from .agent_types import (
    AGENT_TYPES,
    DEFAULT_KIND,
    AgentKind,
    agent_type_view,
    disallowed_tools,
    get_agent_type,
    recommended_tools,
)
from .approvals import ApprovalConflict, ApprovalRecord
from .reports import ReportRecord
from .runner import AgentRunner, RunStore, new_run
from .store import (
    AGENT_ID_PATTERN,
    DEFAULT_MAX_STEPS,
    MAX_FROM_NAME,
    MAX_RECIPIENTS,
    MAX_SCRAPE_DOMAINS,
    MAX_SIGNATURE,
    AgentRecord,
    DuplicateAgentError,
    GatewayStore,
    InvalidEmailSettingsError,
    InvalidRecipientsError,
    InvalidScrapeDomainsError,
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

router = APIRouter()


def _save_report(data: dict[str, Any]) -> dict[str, Any]:
    # Looked up per call: the host app may swap in the DynamoDB repository.
    return store.reports.insert(ReportRecord(**data)).view()


email_handlers.set_report_sink(_save_report)


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
    # AAuth when HOMEFRONT_AUTH_PROTOCOLS includes aauth (else legacy_workload).
    auth_protocol: str = "legacy_workload"
    agent_jwt: str | None = None
    aa_auth_jwt: str | None = None
    jkt: str | None = None


class GrantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no owner/office/user: the caller is the actor

    tools: list[str]


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# Field limits mirror the check constraints on public.agents.
class CreateAgentRequest(BaseModel):
    """`kind` picks a template: instructions, tool_grants and max_steps left
    out of the request take the type's defaults (tool_grants: the recommended
    set the creator's role allows). Fixed after creation. The owner is always the caller."""

    model_config = ConfigDict(extra="forbid")

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
    # web.scrape domain allowlist, e.g. ["*.gov", "example.org"]. Left out =
    # the type's default; [] = any public host.
    scrape_domains: list[str] | None = Field(default=None, max_length=MAX_SCRAPE_DOMAINS * 2)
    # Social platforms the agent is for (x, bluesky, reddit, facebook, linkedin). Shown on its card.
    platforms: list[str] = Field(default_factory=list, max_length=10)


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
    # Replaces the web.scrape domain allowlist; [] removes the restriction.
    scrape_domains: list[str] | None = Field(default=None, max_length=MAX_SCRAPE_DOMAINS * 2)


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
        "scrape_domains": list(agent.scrape_domains),
        "platforms": list(agent.platforms),
        "token_version": agent.token_version,
        "active": agent.active,
    }


def _scrape_domains_error(exc: InvalidScrapeDomainsError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "invalid_scrape_domains", "message": str(exc), "invalid": exc.invalid},
    )


def _recipients_error(exc: InvalidRecipientsError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": "invalid_recipients", "message": str(exc), "invalid": exc.invalid},
    )


# Tools whose audience is limited by the agent's mailing list.
EMAIL_SEND = "email.send"
EMAIL_SEND_LIST = "email.send_list"
EMAIL_TOOLS = (EMAIL_SEND, EMAIL_SEND_LIST)
SOCIAL_TOOLS = ("social.post", "social.reply")
# Tools whose handler needs to know the calling agent (gateway-injected).
CALLER_CONTEXT_TOOLS = frozenset({"report.save"})
WEB_SCRAPE = "web.scrape"


def _scrape_domain_problem(agent: AgentRecord, tool_name: str, arguments: dict[str, Any]) -> str | None:
    """The host a web.scrape call is refused for ("" if the URL has none), or None if allowed."""
    if tool_name != WEB_SCRAPE or not agent.scrape_domains:
        return None
    try:
        host = (urllib.parse.urlsplit(str(arguments.get("url") or "")).hostname or "").rstrip(".").lower()
    except ValueError:
        host = ""
    return None if host and host_matches_domains(host, agent.scrape_domains) else host


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
    """Why this call would email someone outside the agent's list (or name a platform
    with no share link), or None if it's fine."""
    if tool_name == EMAIL_SEND:
        to = normalize_email(str(arguments.get("to") or ""))
        if to not in agent.recipients:
            return f"{to or 'the recipient'} isn't on this agent's mailing list"
    elif tool_name == EMAIL_SEND_LIST and not agent.recipients:
        return "this agent's mailing list is empty"
    elif tool_name in SOCIAL_TOOLS:
        # Social drafts become share links, which exist only for some platforms.
        try:
            normalize_platform(arguments.get("platform"))
        except ShareLinkError as exc:
            return str(exc)
    return None


@dataclass(frozen=True)
class Actor:
    """Who is calling an agent-management route.

    user_id is the Cognito username of the signed-in staffer. Agents with no
    owner are shared demo agents any signed-in caller may see; owned agents are
    visible only to their owner.
    """

    user_id: str | None = None
    # The Cognito admin group. It opens the admin pages and shared agents; it never widens
    # the office role that caps an agent's tools.
    is_admin: bool = False

    def can_access(self, agent: AgentRecord) -> bool:
        return agent.owner_id is None or agent.owner_id == self.user_id

    @property
    def label(self) -> str:
        """How this caller appears in the activity log."""
        return f"user:{self.user_id}" if self.user_id else "anonymous"


ActorResolver = Callable[[str | None], Actor]


def _refuse(_authorization: str | None) -> Actor:
    raise HTTPException(status_code=401, detail="Authentication failed")


# The host app sets this to verify our Cognito login (Backend/app/agent_iam.py).
# Until then every call is refused: there is no anonymous caller (fail closed).
_actor_resolver: ActorResolver = _refuse


def set_actor_resolver(resolver: ActorResolver) -> None:
    global _actor_resolver
    _actor_resolver = resolver


RoleResolver = Callable[[str], StafferItem | None]


def _no_staffer(_user_id: str) -> StafferItem | None:
    return None


# The host app sets this to read the staffer's item fresh from the mandate store
# (Backend/app/agent_iam.py). Until then nobody has a role, so no tool is allowed (fail closed).
_role_resolver: RoleResolver = _no_staffer


def set_role_resolver(resolver: RoleResolver) -> None:
    global _role_resolver
    _role_resolver = resolver


def _office_role(user_id: str | None) -> str | None:
    """The staffer's office role right now, or None if they are unknown, offboarded or have no role."""
    staffer = _role_resolver(user_id) if user_id else None
    return staffer.office_role if staffer and staffer.active else None


def get_actor(authorization: str | None = Header(default=None)) -> Actor:
    return _actor_resolver(authorization)


def _accessible_agent(agent_id: str, actor: Actor) -> AgentRecord:
    agent = store.get_agent(agent_id)
    # 404, not 403, so callers can't probe which agent ids exist.
    if agent is None or not actor.can_access(agent):
        raise HTTPException(status_code=404, detail="Unknown agent")
    return agent


def _editable_agent(agent_id: str, actor: Actor) -> AgentRecord:
    """An agent whose grants the caller may change: their own, or a shared one if they are an admin."""
    agent = _accessible_agent(agent_id, actor)
    if agent.owner_id is None and not actor.is_admin:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "shared_agent_admin_only",
                "message": "Only an admin can change the tools of a shared agent.",
            },
        )
    return agent


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


@router.get("/agent-tools")
def tools_catalog() -> list[dict[str, Any]]:
    """The old gateway's tool catalog (email.send, ...) with ATLAS risk labels.
    `/tools` is the mandate flow's seven-tool catalog (app.py)."""
    return [tool_public_view(t) for t in list_tools()]


@router.get("/agent-types")
def agent_types_catalog() -> list[dict[str, Any]]:
    """Catalog of agent types: templates and tool ceilings."""
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


def _check_tool_names(tools: list[str]) -> None:
    """Raise 400 for a name that isn't exactly a registry tool, or is listed twice.
    No normalising: a lookalike must never turn into a real grant."""
    unknown = [t for t in tools if t not in TOOL_REGISTRY]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"error": "unknown_tools", "message": f"Unknown tools: {unknown}", "tools": unknown},
        )
    duplicates = sorted({t for t in tools if tools.count(t) > 1})
    if duplicates:
        raise HTTPException(
            status_code=400,
            detail={"error": "duplicate_tools", "message": f"Listed twice: {duplicates}", "tools": duplicates},
        )


def _enforce_role_cap(tools: list[str], actor: Actor, agent_id: str | None = None) -> None:
    """Raise 403 (and audit a deny) if the caller's office role doesn't allow any of `tools`.
    The role is read from the staffer's item now, never from the request or the token."""
    role = _office_role(actor.user_id)
    denied = sorted(t for t in tools if not role_allows(role, t))
    if not denied:
        return
    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.deny,
            tool=denied[0],
            agent_id=agent_id,
            reason="tool outside the creator's office role",
        )
    )
    event = store.append_audit(
        {
            "type": "grant_denied",
            "decision": "deny",
            "agent_id": agent_id,
            "actor": actor.label,
            "tool": denied[0],
            "tools": denied,
            "atlas": atlas.model_dump(),
        }
    )
    raise HTTPException(
        status_code=403,
        detail={
            "error": "role_not_allowed",
            "message": f"Your office role ({role_label(role)}) can't give an agent {', '.join(denied)}.",
            "tools": denied,
            "atlas": atlas.model_dump(),
            "audit_id": event["id"],
        },
    )


def _capping_user(agent: AgentRecord, run_id: str | None, acting_user: str | None) -> str | None:
    """Whose office role caps this agent's tool calls: its owner. A shared agent has no owner, so
    the staffer who started the run, else the staffer making the call; None (denied) if neither is known."""
    if agent.owner_id:
        return agent.owner_id
    run = runs.get(run_id) if run_id else None
    return (run.created_by if run else None) or acting_user


def _reason_unavailable(kind: str, role: str | None, tool_name: str, kind_ok: bool, role_ok: bool) -> str | None:
    if not kind_ok:
        return f"{get_agent_type(kind).label} agents can't hold this tool."
    if not role_ok:
        reason = role_reason(role, mandate_tool_name(tool_name))
        return reason[0].upper() + reason[1:] + "."
    return None


@router.get("/agent-permissions")
def agent_permissions(kind: AgentKind, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """The permission picker's catalog: every tool with whether this caller may give it to an agent
    of `kind` (kind ceiling AND the caller's office role) and whether it is recommended."""
    agent_type = get_agent_type(kind)
    role = _office_role(actor.user_id)
    tools = []
    for name, tool in TOOL_REGISTRY.items():
        kind_ok = agent_type.allows(name)
        role_ok = role_allows(role, name)
        tools.append(
            {
                "name": name,
                "label": name.replace(".", " ").replace("_", " ").capitalize(),
                "effect": TOOL_ACTIONS[mandate_tool_name(name)].capitalize(),
                "risk": tool.risk_level,
                "outbound": tool.outbound,
                "held_for_review": tool.requires_hitl,
                "allowed_for_kind": kind_ok,
                "allowed_for_role": role_ok,
                "recommended": kind_ok and role_ok and name in recommended_tools(kind),
                "reason_unavailable": _reason_unavailable(kind, role, name, kind_ok, role_ok),
            }
        )
    return {"kind": kind, "role": role, "role_label": role_label(role), "tools": tools}


@router.get("/agents")
def agents(actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    return [_agent_view(a) for a in store.list_agents() if actor.can_access(a)]


@router.post("/agents", status_code=201)
def create_agent(
    body: CreateAgentRequest, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    agent_type = get_agent_type(body.kind)
    sent = body.model_fields_set
    role = _office_role(actor.user_id)
    tool_grants = (
        body.tool_grants
        if "tool_grants" in sent
        else sorted(t for t in recommended_tools(body.kind) if role_allows(role, t))
    )
    instructions = (
        body.instructions if "instructions" in sent else agent_type.default_instructions
    )
    max_steps = body.max_steps if "max_steps" in sent else agent_type.default_max_steps
    scrape_domains = (
        body.scrape_domains
        if body.scrape_domains is not None
        else list(agent_type.default_scrape_domains)
    )

    try:
        platforms = list(dict.fromkeys(normalize_platform(p) for p in body.platforms))
    except ShareLinkError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_platforms", "message": str(exc)})
    if platforms and body.kind != "social":
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_platforms", "message": "Only social media agents have platforms."},
        )

    _check_tool_names(tool_grants)
    blocked = disallowed_tools(body.kind, tool_grants)
    if blocked:
        # No agent exists yet, so nothing to audit against.
        raise HTTPException(status_code=403, detail=_kind_denial(body.kind, blocked))
    _enforce_role_cap(tool_grants, actor)
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
            scrape_domains=scrape_domains,
            platforms=platforms,
        )
    except InvalidRecipientsError as exc:
        raise _recipients_error(exc)
    except InvalidScrapeDomainsError as exc:
        raise _scrape_domains_error(exc)
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


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    return _agent_view(_accessible_agent(agent_id, actor))


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: str, body: UpdateAgentRequest, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    before = _accessible_agent(agent_id, actor)
    # exclude_unset: only fields the caller sent. "model": null resets to the
    # runner default; null for any other field is ignored.
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k == "model"}
    domains = changes.pop("scrape_domains", None)
    try:
        if domains is not None:
            # Validated before any other field changes, so a bad list changes nothing.
            agent = store.set_scrape_domains(agent_id, domains)
            if agent.scrape_domains != before.scrape_domains:
                changes["scrape_domains"] = agent.scrape_domains
        agent = store.update_agent(agent_id, **{k: v for k, v in changes.items() if k != "scrape_domains"})
    except InvalidScrapeDomainsError as exc:
        raise _scrape_domains_error(exc)
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


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """Delete one of your own agents. Shared demo agents (no owner) can't be deleted.

    Requests waiting for approval are rejected, and the agent's tokens stop
    working immediately. The activity log keeps its history.
    """
    agent = _accessible_agent(agent_id, actor)
    if agent.owner_id is None or agent.owner_id != actor.user_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "cannot_delete_shared_agent", "message": "Shared demo agents can't be deleted."},
        )
    rejected = 0
    for rec in store.approvals.list():
        if rec.agent_id == agent_id and rec.status == "pending":
            try:
                store.approvals.transition(
                    rec.approval_id,
                    "pending",
                    status="rejected",
                    decided_by=actor.label,
                    decided_at=time.time(),
                    note="Agent deleted",
                )
                rejected += 1
            except ApprovalConflict:
                pass  # decided in the meantime
    try:
        store.delete_agent(agent_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown agent")
    store.append_audit(
        {
            "type": "agent_deleted",
            "decision": "info",
            "agent_id": agent_id,
            "actor": actor.label,
            "kind": agent.kind,
            "detail": f"Deleted {agent.kind} agent \"{agent.name}\""
            + (f"; rejected {rejected} pending approval{'s' if rejected != 1 else ''}" if rejected else ""),
        }
    )
    return {"deleted": agent_id, "rejected_approvals": rejected}


@router.put("/agents/{agent_id}/recipients")
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


@router.put("/agents/{agent_id}/email-settings")
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


@router.get("/agents/{agent_id}/email-settings/verification")
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


@router.get("/email/account")
def email_account(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """SES sandbox status and sending quota (cached ~60s). Nulls + reason when it can't be read."""
    return email_admin.account_status()


@router.get("/email/permissions")
def email_permissions(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """Whether the server's AWS role may send email / request verification (IAM simulation)."""
    return email_admin.permissions()


@router.post("/agents/{agent_id}/email-settings/verify-sender")
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


@router.post("/agents/{agent_id}/email-settings/test")
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


@router.get("/agents/{agent_id}/recipients/verification")
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


@router.post("/agents/{agent_id}/recipients/verify")
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


@router.post("/auth/token", response_model=AuthResponse)
def authenticate(body: AuthRequest, actor: Actor = Depends(get_actor)) -> AuthResponse:
    agent = _accessible_agent(body.agent_id, actor)
    if not agent.active:
        raise HTTPException(status_code=403, detail="Agent is inactive")
    tok = store.issue_token(
        agent.agent_id,
        body.ttl_seconds,
        acting_for=actor.user_id or agent.agent_id,
        office=agent.agency,
    )
    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.authenticate,
            agent_id=agent.agent_id,
            reason="workload identity issued",
        )
    )
    aauth_on = tok.auth_protocol == "aauth"
    narrative = (
        "Agent authenticated with AAuth (aa-agent+jwt + PoP-bound aa-auth+jwt); "
        "tool grants enforced per AML.M0028."
        if aauth_on
        else atlas.narrative
    )
    atlas_out = {**atlas.model_dump(), "narrative": narrative}
    # Auth itself is not a tool call; still log under AML.M0024 telemetry.
    store.append_audit(
        {
            "type": "authenticate",
            "decision": "allow",
            "agent_id": agent.agent_id,
            "trace_id": tok.session_id,
            "actor": actor.label,
            "tool": None,
            "auth_protocol": tok.auth_protocol,
            "atlas": atlas_out,
        }
    )
    return AuthResponse(
        access_token=tok.token,
        agent_id=agent.agent_id,
        agency=agent.agency,
        tool_grants=sorted(agent.tool_grants),
        token_version=tok.token_version,
        session_id=tok.session_id,
        auth_protocol=tok.auth_protocol,
        agent_jwt=tok.agent_jwt,
        aa_auth_jwt=tok.aa_auth_jwt,
        jkt=tok.jkt,
        atlas={
            "technique_ids": atlas.technique_ids,
            "mitigation_ids": atlas.mitigation_ids,
            "narrative": narrative,
        },
    )


@router.put("/agents/{agent_id}/grants")
def replace_grants(
    agent_id: str, body: GrantUpdate, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    current = _editable_agent(agent_id, actor)
    _check_tool_names(body.tools)
    _enforce_kind_ceiling(current, body.tools, actor)
    _enforce_role_cap(body.tools, actor, agent_id)
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


@router.post("/agents/{agent_id}/grants/{tool_name}/revoke")
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


@router.post("/agents/{agent_id}/grants/{tool_name}")
def grant_tool(
    agent_id: str, tool_name: str, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    current = _editable_agent(agent_id, actor)
    _check_tool_names([tool_name])
    _enforce_kind_ceiling(current, [tool_name], actor)
    _enforce_role_cap([tool_name], actor, agent_id)
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


@router.post("/agents/tool-call")
def agent_tool_call(
    body: ToolCallRequest,
    x_agent_token: str | None = Header(default=None),
    actor: Actor = Depends(get_actor),
) -> dict[str, Any]:
    """Monitoring page's manual tool call: an admin login (Authorization) AND the
    agent's workload token (X-Agent-Token). Same grant re-check as the runner.
    `/mcp/call` is the mandate flow's dashboard path (app.py), not this."""
    if not x_agent_token:
        raise HTTPException(status_code=401, detail="Missing agent token")
    agent, tok = resolve_agent_token(x_agent_token.strip())
    if not actor.can_access(agent):
        raise HTTPException(status_code=404, detail="Unknown agent")
    return call_tool(agent, tok, body.tool, body.arguments, acting_user=actor.user_id)


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
    acting_user: str | None = None,
) -> dict[str, Any]:
    """Authorize, execute and audit one tool call. Raises HTTPException on deny.
    `acting_user` is the signed-in staffer behind the call, if any (see _capping_user)."""
    from .workload_aauth import prove_workload_aauth, tool_call_proof_body

    try:
        auth_protocol = prove_workload_aauth(
            tok,
            method="POST",
            path="/api/v1/agents/tool-call",
            body=tool_call_proof_body(tool_name, arguments),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=401,
            detail={"error": "aauth_pop_failed", "message": str(exc)},
        ) from exc

    def _tool_audit(payload: dict[str, Any]) -> dict[str, Any]:
        return store.append_audit({**payload, "auth_protocol": auth_protocol})

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
        event = _tool_audit(
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
        event = _tool_audit(
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
        event = _tool_audit(
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

    # A grant is only as wide as the owner's office role is today: a demoted or
    # offboarded owner's agents lose the tool at once, whatever was granted.
    if not role_allows(_office_role(_capping_user(agent, run_id, acting_user)), tool_name):
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=tool_name,
                agent_id=agent.agent_id,
                reason="tool outside the office role of the staffer behind this agent",
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
                "error": "role_not_allowed",
                "message": f"Blocked {tool_name}: the staffer behind this agent no longer has an office role that allows it.",
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    trace_id = run_id or getattr(tok, "session_id", None)

    # Information-flow control: after this run/session read untrusted content,
    # nothing may leave the agency, whatever the agent's type or grants.
    taint = store.taint_of(trace_id) if tool.outbound else None
    if taint:
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.tainted_session,
                tool=tool_name,
                agent_id=agent.agent_id,
                reason=f"{taint['tool']} returned untrusted content earlier in this trace",
            )
        )
        event = _tool_audit(
            {
                "type": "tool_call",
                "run_id": run_id,
                "trace_id": trace_id,
                "actor": f"agent:{agent.agent_id}",
                "decision": "deny",
                "reason": "tainted_session",
                "agent_id": agent.agent_id,
                "tool": tool_name,
                "tainted_by": taint["tool"],
                "args_hash": _hash_args(arguments),
                "atlas": atlas.model_dump(),
                "detail": f"Blocked: this trace read untrusted content via {taint['tool']}",
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tainted_session",
                "message": (
                    f"Blocked {tool_name}: this run already read untrusted content via "
                    f"{taint['tool']}, so outbound tools are disabled for it (AML.M0030). "
                    "Start a new run to send, or hand off to a person."
                ),
                "tainted_by": taint["tool"],
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    # Argument-level authorization: an agent with a domain policy may only
    # scrape matching hosts (redirects are checked again by the handler).
    blocked_host = _scrape_domain_problem(agent, tool_name, arguments)
    if blocked_host is not None:
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=tool_name,
                agent_id=agent.agent_id,
                reason=f"{blocked_host or 'URL'} outside agent scrape_domains",
            )
        )
        event = _tool_audit(
            {
                "type": "tool_call",
                "run_id": run_id,
                "trace_id": trace_id,
                "actor": f"agent:{agent.agent_id}",
                "decision": "deny",
                "reason": "domain_not_allowed",
                "agent_id": agent.agent_id,
                "tool": tool_name,
                "args_hash": _hash_args(arguments),
                "atlas": atlas.model_dump(),
                "detail": f"Blocked: {blocked_host or 'URL'} isn't in {', '.join(agent.scrape_domains)}",
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "domain_not_allowed",
                "message": (
                    f"Blocked {tool_name}: {blocked_host or 'that URL'} is outside this agent's "
                    f"allowed domains ({', '.join(agent.scrape_domains)}) (AML.M0028)."
                ),
                "host": blocked_host or None,
                "allowed_domains": list(agent.scrape_domains),
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    # Outbound email only reaches the agent's mailing list. Checked before
    # queuing so an injected recipient never even reaches a reviewer.
    problem = _recipient_problem(agent, tool_name, arguments)
    if problem:
        social = tool_name in SOCIAL_TOOLS
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=tool_name,
                agent_id=agent.agent_id,
                reason="unsupported social platform" if social else "recipient not on agent mailing list",
            )
        )
        event = _tool_audit(
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
                "error": "platform_not_supported" if social else "recipient_not_allowed",
                "message": f"Blocked {tool_name}: {problem} (AML.M0028).",
                "atlas": atlas.model_dump(),
                "audit_id": event["id"],
            },
        )

    # High-risk outbound tools wait for a person (AML.M0029).
    if tool.requires_hitl:
        return _request_approval(agent, tok, tool_name, arguments, run_id, auth_protocol=auth_protocol)

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
    run_args.pop("allowed_domains", None)  # gateway-injected only
    if tool_name == WEB_SCRAPE and agent.scrape_domains:
        run_args["allowed_domains"] = list(agent.scrape_domains)
    if tool_name in CALLER_CONTEXT_TOOLS:
        # Who is calling comes from the workload token, never from arguments.
        run_args.update(
            agent_id=agent.agent_id,
            trace_id=trace_id,
            run_id=run_id,
        )
    result = _run_handler(tool, tool_name, run_args)
    if tool.untrusted_input:
        # Taint even if the handler refused the URL: conservative and simple.
        store.mark_tainted(trace_id, tool_name)

    event = _tool_audit(
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
        "auth_protocol": auth_protocol,
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
    agent: AgentRecord,
    tok: Any,
    tool_name: str,
    arguments: dict[str, Any],
    run_id: str | None,
    *,
    auth_protocol: str | None = None,
) -> dict[str, Any]:
    trace_id = run_id or getattr(tok, "session_id", None)
    protocol = auth_protocol or getattr(tok, "auth_protocol", None) or "legacy_workload"
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
            "auth_protocol": protocol,
            "atlas": HITL_ATLAS,
            "detail": f"Waiting for a person to approve {tool_name}",
        }
    )
    return {
        "decision": "pending_approval",
        "tool": tool_name,
        "agent_id": agent.agent_id,
        "token_version": tok.token_version,
        "auth_protocol": protocol,
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


@router.get("/approvals")
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


@router.get("/approvals/{approval_id}")
def get_approval(approval_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    rec, agent = _accessible_approval(approval_id, actor)
    return _approval_view(rec, agent)


@router.post("/approvals/{approval_id}/approve")
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
    # Taint is deliberately not re-checked: a request can only exist if its
    # trace was clean when the agent made it, so its arguments were written
    # before any untrusted content entered the context, and a person reviews
    # (and may edit) them now. Untrusted reads later in the same run don't
    # change what was queued.
    blocked_reason = None
    if not agent.active:
        blocked_reason = "the agent is inactive"
    elif rec.tool not in agent.tool_grants:
        blocked_reason = f"{rec.tool} was revoked"
    elif disallowed_tools(agent.kind, [rec.tool]):
        blocked_reason = f"{rec.tool} isn't allowed for {agent.kind} agents"
    elif not role_allows(_office_role(_capping_user(agent, rec.run_id, actor.user_id)), rec.tool):
        blocked_reason = f"the office role of the staffer behind this agent no longer allows {rec.tool}"
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
            "detail": (
                f"Approved by {actor.label}; share link handed over, not published by HomeFront"
                if result.get("status") == "handed_off"
                else f"Executed after approval by {actor.label}"
            ),
        }
    )
    return _approval_view(done, agent)


@router.post("/approvals/{approval_id}/reject")
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
    # When true on an email (or email-capable) agent: research agent runs first,
    # then this agent drafts/sends from those findings only.
    research_first: bool = False
    research_agent_id: str | None = None


def configure_runner(new_runner: AgentRunner | None) -> None:
    global runner
    runner = new_runner


# Set by the host app on AWS Lambda: hands a queued run (by id) to its own invocation, since
# Lambda freezes a process once it has answered. None = run after the response (BackgroundTasks).
run_dispatcher: Callable[[str], None] | None = None
RUN_AUTOSAVE_SECONDS = 2.0
_log = logging.getLogger(__name__)


def configure_run_dispatcher(dispatcher: Callable[[str], None] | None) -> None:
    global run_dispatcher
    run_dispatcher = dispatcher


def execute_queued_run(run_id: str) -> None:
    """Execute a run that `start_run` queued (the dispatcher's worker side).

    Runs only a run still `queued`, so a retried delivery never executes it twice. Progress is
    saved every few seconds, so GET .../runs/{run_id} from any process shows the steps so far.
    """
    run = runs.get(run_id)
    if run is None or run.status != "queued":
        _log.info("run %s not queued (%s); skipping", run_id, run.status if run else "missing")
        return
    agent = store.get_agent(run.agent_id)
    research_agent = store.get_agent(run.research_agent_id) if run.research_agent_id else None
    if runner is None or agent is None or (run.pipeline and research_agent is None):
        run.status = "failed"
        run.error = "Agent runner is not configured" if runner is None else "Agent no longer exists"
        run.finished_at = time.time()
        runs.save(run)
        return

    stop = threading.Event()

    def autosave() -> None:
        while not stop.wait(RUN_AUTOSAVE_SECONDS):
            try:
                runs.save(run)
            except Exception:  # the run mutates while we copy it; the next save catches up
                _log.debug("run %s autosave skipped", run_id, exc_info=True)

    saver = threading.Thread(target=autosave, name=f"run-autosave-{run_id}", daemon=True)
    saver.start()
    try:
        if research_agent is not None:
            _execute_pipeline_and_audit(runner, run, agent, research_agent)
        else:
            _execute_and_audit(runner, run, agent)
    finally:
        stop.set()
        saver.join()
        runs.save(run)


def _issue_run_token(agent_id: str, run_id: str | None = None) -> str:
    agent = store.get_agent(agent_id)
    tok = store.issue_token(
        agent_id,
        ttl_seconds=3600,
        session_id=run_id,
        acting_for=agent_id,
        office=(agent.agency if agent else None),
    )
    store.append_audit(
        {
            "type": "authenticate",
            "decision": "allow",
            "agent_id": agent_id,
            "run_id": run_id,
            "trace_id": tok.session_id,
            "actor": f"agent:{agent_id}",
            "tool": None,
            "auth_protocol": tok.auth_protocol,
            "reason": "workload token issued for agent run",
        }
    )
    return tok.token


def _resolve_research_agent(actor: Actor, research_agent_id: str | None) -> AgentRecord:
    if research_agent_id:
        agent = _accessible_agent(research_agent_id, actor)
        if agent.kind != "research":
            raise HTTPException(
                status_code=400,
                detail="research_agent_id must refer to a research-kind agent",
            )
        if not agent.active:
            raise HTTPException(status_code=403, detail="Research agent is inactive")
        return agent
    for agent in store.list_agents():
        if agent.kind == "research" and agent.active and actor.can_access(agent):
            return agent
    raise HTTPException(
        status_code=400,
        detail=(
            "No research agent available. Create a research-kind agent or pass research_agent_id."
        ),
    )


@router.post("/agents/{agent_id}/runs", status_code=202)
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

    research_agent: AgentRecord | None = None
    if body.research_first:
        research_agent = _resolve_research_agent(actor, body.research_agent_id)

    run = runs.add(new_run(agent, body.task, runner.model_for(agent), actor.user_id))
    if research_agent is not None:
        run.pipeline = "research_then_email"
        run.research_agent_id = research_agent.agent_id

    store.append_audit(
        {
            "type": "run_started",
            "decision": "info",
            "agent_id": agent.agent_id,
            "run_id": run.run_id,
            "trace_id": run.run_id,
            "actor": actor.label,
            "detail": body.task[:200],
            "pipeline": run.pipeline,
            "research_agent_id": run.research_agent_id,
        }
    )
    if run_dispatcher is not None:
        runs.save(run)  # pipeline fields were set after add
        run_dispatcher(run.run_id)
    elif research_agent is not None:
        background.add_task(_execute_pipeline_and_audit, runner, run, agent, research_agent)
    else:
        background.add_task(_execute_and_audit, runner, run, agent)
    return run.view()


def _execute_and_audit(active_runner: AgentRunner, run: Any, agent: AgentRecord) -> None:
    active_runner.execute(run, agent)
    _audit_run_finished(run, agent)


def _execute_pipeline_and_audit(
    active_runner: AgentRunner,
    run: Any,
    email_agent: AgentRecord,
    research_agent: AgentRecord,
) -> None:
    from .pipeline import execute_research_then_email

    execute_research_then_email(
        active_runner, run, email_agent, research_agent, add_run=runs.add
    )
    _audit_run_finished(run, email_agent)


def _audit_run_finished(run: Any, agent: AgentRecord) -> None:
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
            "pipeline": getattr(run, "pipeline", None),
            "research_run_id": getattr(run, "research_run_id", None),
            "detail": run.error or f"Finished in {run.model_turns} model turn(s)",
        }
    )


@router.get("/agents/{agent_id}/runs")
def list_runs(agent_id: str, actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    _accessible_agent(agent_id, actor)
    return [r.view() for r in runs.for_agent(agent_id)]


@router.get("/agents/{agent_id}/runs/{run_id}")
def get_run(agent_id: str, run_id: str, actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    _accessible_agent(agent_id, actor)
    run = runs.get(run_id)
    if run is None or run.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Unknown run")
    return run.view()


@router.get("/agents/{agent_id}/reports")
def list_reports(
    agent_id: str, limit: int = 50, actor: Actor = Depends(get_actor)
) -> list[dict[str, Any]]:
    """Reports this agent saved with report.save, newest first."""
    _accessible_agent(agent_id, actor)
    limit = max(1, min(limit, 200))
    return [r.view() for r in store.reports.list(agent_id, limit)]


@router.get("/audit")
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


@router.get("/status")
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


def _hash_args(args: dict[str, Any]) -> str:
    blob = repr(sorted(args.items())).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]
