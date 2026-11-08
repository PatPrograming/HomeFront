"""Mount MITRE ATLAS agent IAM routes onto the HomeFront API.

User auth is Amazon Cognito (`app.core.auth`). Agent workload identity,
MCP tool grants/revoke, and ATLAS enrichment live in `packages/identity-gateway`
and are attached here so one FastAPI process serves both, all under `/api/v1`.

Route groups
- admin: /agents*, /auth/token, /audit  -- create agents, change grants, mint
  agent tokens, read the audit log. Scoped to the caller:
  - signed-in user: their own agents plus ownerless (shared demo) agents;
    agents they create are owned by them.
  - no login: only ownerless agents, and only while
    `AGENT_ADMIN_REQUIRES_LOGIN=false` (standalone demo console).
  - invalid/expired user token: always 401, never downgraded to anonymous.
- open: /mcp/call and /auth/me (authenticated by the *agent* Bearer token, not
  a user), plus the public catalogs /tools, /atlas/*, /demo/matrix.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, status
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import get_current_user
from app.core.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_SRC = [
    _REPO_ROOT / "packages" / "atlas-enricher" / "src",
    _REPO_ROOT / "packages" / "mcp-tools" / "src",
    _REPO_ROOT / "packages" / "identity-gateway" / "src",
]

# Gateway paths that manage agents rather than act as one.
_ADMIN_PREFIXES = ("/agents", "/auth/token", "/audit")
# Served by the host app's own router instead.
_SKIP_PATHS = {"/health"}


def _ensure_package_paths() -> None:
    for path in _PACKAGE_SRC:
        resolved = str(path)
        if path.is_dir() and resolved not in sys.path:
            sys.path.insert(0, resolved)


def resolve_user_id(authorization: str | None) -> str | None:
    """Cognito user id (`sub`) for an agent-management request, or None if anonymous.

    Anonymous is allowed only when `AGENT_ADMIN_REQUIRES_LOGIN=false`. A token
    that is present but invalid is always rejected.
    """
    if not authorization:
        if get_settings().agent_admin_requires_login:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token.strip())
    return get_current_user(creds).id


def require_agent_admin(authorization: str | None = Header(default=None)) -> None:
    """Router-level gate on every admin route, so a route that forgets to ask
    for the actor is still authenticated."""
    resolve_user_id(authorization)


def _is_admin_path(path: str) -> bool:
    return path.startswith(_ADMIN_PREFIXES)


def include_agent_iam(host_app: FastAPI, prefix: str = "") -> None:
    """Attach agent authZ + ATLAS routes (auth/token, mcp/call, atlas/*, ...) under `prefix`."""
    _ensure_package_paths()
    from atlas_enricher.router import build_router
    from identity_gateway.app import app as agent_app
    from identity_gateway.app import Actor, enricher, set_actor_resolver, store

    set_actor_resolver(lambda authorization: Actor(user_id=resolve_user_id(authorization)))
    configure_bedrock_runner()

    if get_settings().agent_store == "dynamodb":
        from identity_gateway.dynamo_repo import DynamoAgentRepository, seed_demo_agent

        from app.core.dynamodb import table

        repo = DynamoAgentRepository(table("agents"))
        seed_demo_agent(repo)  # replaces the SQL migration's seed row
        store.use_agent_repository(repo)

    # The gateway nests the ATLAS router, so it isn't in agent_app.routes; mount it directly.
    host_app.include_router(build_router(enricher), prefix=prefix)

    admin_router = APIRouter()
    open_router = APIRouter()
    for route in agent_app.routes:
        if not isinstance(route, APIRoute) or route.path in _SKIP_PATHS:
            continue
        (admin_router if _is_admin_path(route.path) else open_router).routes.append(route)

    host_app.include_router(open_router, prefix=prefix)
    host_app.include_router(
        admin_router, prefix=prefix, dependencies=[Depends(require_agent_admin)]
    )


def configure_bedrock_runner() -> None:
    """Attach the Claude-on-Bedrock runner if a region is configured."""
    from identity_gateway import app as gateway
    from identity_gateway.runner import AgentRunner, RunnerConfig

    settings = get_settings()
    region = settings.resolved_bedrock_region
    if not region:
        gateway.configure_runner(None)
        return

    def make_client():
        from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware

        middleware = []
        if settings.bedrock_fallback_model_id:
            # Bedrock has no server-side `fallbacks`; the SDK middleware retries
            # a refused request on the fallback model client-side.
            middleware.append(
                BetaRefusalFallbackMiddleware([{"model": settings.bedrock_fallback_model_id}])
            )
        return AnthropicBedrockMantle(
            aws_region=region,
            api_key=settings.aws_bearer_token_bedrock or None,
            aws_profile=settings.aws_profile or None,
            middleware=middleware,
        )

    def fallback_state():
        from anthropic import BetaFallbackState

        return BetaFallbackState()

    gateway.configure_runner(
        AgentRunner(
            client_factory=make_client,
            call_tool=gateway.gateway_tool_call,
            issue_token=gateway._issue_run_token,
            config=RunnerConfig(
                default_model=settings.bedrock_model_id,
                effort=settings.runner_effort,
            ),
            fallback_state_factory=fallback_state,
        )
    )
