from __future__ import annotations

import hashlib
import sys
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
from mcp_tools.handlers import invoke_tool  # noqa: E402
from mcp_tools.registry import get_tool, list_tools, tool_public_view  # noqa: E402

from .runner import AgentRunner, RunStore, new_run
from .store import (
    AGENT_ID_PATTERN,
    DEFAULT_MAX_STEPS,
    AgentRecord,
    DuplicateAgentError,
    GatewayStore,
)

# Agents live in memory by default; the host app can swap in DynamoDB via
# store.use_agent_repository(...).
store = GatewayStore()
runs = RunStore()
# Set by the host app (see Backend/app/agent_iam.py); None = runs return 503.
runner: AgentRunner | None = None
enricher = AtlasEnricher()

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
    atlas: dict[str, Any]


class GrantUpdate(BaseModel):
    tools: list[str]


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# Field limits mirror the check constraints on public.agents.
class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    agency: str = Field(min_length=1, max_length=120)
    instructions: str = Field(default="", max_length=8000)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=50)
    tool_grants: list[str] = Field(default_factory=list)
    agent_id: str | None = Field(default=None, pattern=AGENT_ID_PATTERN)


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
        "name": agent.name,
        "agency": agent.agency,
        "instructions": agent.instructions,
        "model": agent.model,
        "max_steps": agent.max_steps,
        "owner_id": agent.owner_id,
        "tool_grants": sorted(agent.tool_grants),
        "token_version": agent.token_version,
        "active": agent.active,
    }


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


@app.get("/agents")
def agents(actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    return [_agent_view(a) for a in store.list_agents() if actor.can_access(a)]


@app.post("/agents", status_code=201)
def create_agent(
    body: CreateAgentRequest, actor: Actor = Depends(get_actor)
) -> dict[str, Any]:
    unknown = [t for t in body.tool_grants if get_tool(t) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools: {unknown}")
    try:
        agent = store.create_agent(
            body.name,
            body.agency,
            body.tool_grants,
            body.agent_id,
            instructions=body.instructions,
            model=body.model,
            max_steps=body.max_steps,
            owner_id=actor.user_id,
        )
    except DuplicateAgentError:
        raise HTTPException(status_code=409, detail=f"Agent {body.agent_id} already exists")
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
    return _agent_view(agent)


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
    _accessible_agent(agent_id, actor)
    unknown = [t for t in body.tools if get_tool(t) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools: {unknown}")
    agent = store.set_grants(agent_id, body.tools)
    atlas = enricher.annotate(
        AnnotateRequest(event=AnnotateEvent.revoke, agent_id=agent_id, reason="grants replaced")
    )
    store.append_audit(
        {
            "type": "grants_replaced",
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
    _accessible_agent(agent_id, actor)
    if get_tool(tool_name) is None:
        raise HTTPException(status_code=404, detail="Unknown tool")
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
    result = invoke_tool(tool_name, arguments)

    # Hard guarantee: never leak gateway-held api keys.
    if tool.holds_secrets:
        result = {**result, "credentials_exposed": False}
        if "api_key" in result or "X-Api-Key" in result:
            result.pop("api_key", None)
            result.pop("X-Api-Key", None)

    event = store.append_audit(
        {
            "type": "tool_call",
            "run_id": run_id,
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


class CreateRunRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)


def configure_runner(new_runner: AgentRunner | None) -> None:
    global runner
    runner = new_runner


def _issue_run_token(agent_id: str) -> str:
    tok = store.issue_token(agent_id, ttl_seconds=3600)
    store.append_audit(
        {
            "type": "authenticate",
            "decision": "allow",
            "agent_id": agent_id,
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
    background.add_task(runner.execute, run, agent)
    return run.view()


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


@app.get("/audit")
def audit(limit: int = 100, actor: Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    visible = {a.agent_id for a in store.list_agents() if actor.can_access(a)}
    limit = max(0, min(limit, 500))
    events = [e for e in store.list_audit(limit=500) if e.get("agent_id") in visible]
    return events[:limit]


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
