from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
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

from .store import InMemoryStore

store = InMemoryStore()
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


class CreateAgentRequest(BaseModel):
    name: str
    agency: str
    tool_grants: list[str] = Field(default_factory=list)
    agent_id: str | None = None


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    return authorization.removeprefix("Bearer ").strip()


def require_agent(authorization: str | None = Header(default=None)):
    token = _bearer(authorization)
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
        "agents": len(store.agents),
    }


@app.get("/tools")
def tools_catalog() -> list[dict[str, Any]]:
    """Public tool catalog with ATLAS risk labels (no secrets)."""
    return [tool_public_view(t) for t in list_tools()]


@app.get("/agents")
def agents() -> list[dict[str, Any]]:
    return [
        {
            "agent_id": a.agent_id,
            "name": a.name,
            "agency": a.agency,
            "tool_grants": sorted(a.tool_grants),
            "token_version": a.token_version,
            "active": a.active,
        }
        for a in store.list_agents()
    ]


@app.post("/agents")
def create_agent(body: CreateAgentRequest) -> dict[str, Any]:
    unknown = [t for t in body.tool_grants if get_tool(t) is None]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tools: {unknown}")
    agent = store.create_agent(body.name, body.agency, body.tool_grants, body.agent_id)
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "agency": agent.agency,
        "tool_grants": sorted(agent.tool_grants),
        "token_version": agent.token_version,
    }


@app.post("/auth/token", response_model=AuthResponse)
def authenticate(body: AuthRequest) -> AuthResponse:
    agent = store.get_agent(body.agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Unknown agent")
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
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "agency": agent.agency,
        "tool_grants": sorted(agent.tool_grants),
        "token_version": tok.token_version,
    }


@app.put("/agents/{agent_id}/grants")
def replace_grants(agent_id: str, body: GrantUpdate) -> dict[str, Any]:
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Unknown agent")
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
def revoke_tool(agent_id: str, tool_name: str) -> dict[str, Any]:
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Unknown agent")
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
def grant_tool(agent_id: str, tool_name: str) -> dict[str, Any]:
    if not store.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Unknown agent")
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
    tool = get_tool(body.tool)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Unknown tool {body.tool}")

    # Secret probe: refuse any attempt to request credentials via args.
    if any(k.lower() in {"api_key", "x-api-key", "secret", "token"} for k in body.arguments):
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.secret_probe,
                tool=body.tool,
                agent_id=agent.agent_id,
            )
        )
        event = store.append_audit(
            {
                "type": "tool_call",
                "decision": "deny",
                "agent_id": agent.agent_id,
                "tool": body.tool,
                "args_hash": _hash_args(body.arguments),
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

    if body.tool not in agent.tool_grants:
        atlas = enricher.annotate(
            AnnotateRequest(
                event=AnnotateEvent.deny,
                tool=body.tool,
                agent_id=agent.agent_id,
                reason="tool not in grant set",
            )
        )
        event = store.append_audit(
            {
                "type": "tool_call",
                "decision": "deny",
                "agent_id": agent.agent_id,
                "tool": body.tool,
                "args_hash": _hash_args(body.arguments),
                "atlas": atlas.model_dump(),
            }
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tool_not_granted",
                "message": f"Blocked {body.tool}: not in agent grants (AML.T0053 / AML.M0028).",
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
                tool=body.tool,
                agent_id=agent.agent_id,
            )
        )
    else:
        untrusted = None

    atlas = enricher.annotate(
        AnnotateRequest(
            event=AnnotateEvent.allow,
            tool=body.tool,
            agent_id=agent.agent_id,
        )
    )
    result = invoke_tool(body.tool, body.arguments)

    # Hard guarantee: never leak gateway-held api keys.
    if tool.holds_secrets:
        result = {**result, "credentials_exposed": False}
        if "api_key" in result or "X-Api-Key" in result:
            result.pop("api_key", None)
            result.pop("X-Api-Key", None)

    event = store.append_audit(
        {
            "type": "tool_call",
            "decision": "allow",
            "agent_id": agent.agent_id,
            "tool": body.tool,
            "args_hash": _hash_args(body.arguments),
            "atlas": atlas.model_dump(),
            "untrusted_input_atlas": untrusted.model_dump() if untrusted else None,
        }
    )
    return {
        "decision": "allow",
        "tool": body.tool,
        "agent_id": agent.agent_id,
        "token_version": tok.token_version,
        "result": result,
        "atlas": atlas.model_dump(),
        "untrusted_input_atlas": untrusted.model_dump() if untrusted else None,
        "audit_id": event["id"],
    }


@app.get("/audit")
def audit(limit: int = 100) -> list[dict[str, Any]]:
    return store.list_audit(limit=min(limit, 500))


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
