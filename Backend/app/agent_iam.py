"""Mount MITRE ATLAS agent IAM routes onto the HomeFront API.

User auth stays on Supabase (`app.core.auth`). Agent workload identity,
MCP tool grants/revoke, and ATLAS enrichment live in `packages/identity-gateway`
and are attached here so one FastAPI process serves both, all under `/api/v1`.

Route groups
- admin (Supabase login required when `AGENT_ADMIN_REQUIRES_LOGIN=true`):
  /agents*, /auth/token, /audit  -- who may create agents, change grants,
  mint agent tokens, and read the audit log.
- open: /mcp/call and /auth/me (authenticated by the *agent* Bearer token, not
  a user), plus the public catalogs /tools, /atlas/*, /demo/matrix.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import CurrentUser, bearer, get_current_user
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


def require_agent_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser | None:
    """Require a logged-in Supabase user for agent management routes.

    Controlled by `AGENT_ADMIN_REQUIRES_LOGIN` so the standalone demo console
    keeps working until it sends a user session.
    """
    if not get_settings().agent_admin_requires_login:
        return None
    return get_current_user(creds)


def _is_admin_path(path: str) -> bool:
    return path.startswith(_ADMIN_PREFIXES)


def include_agent_iam(host_app: FastAPI, prefix: str = "") -> None:
    """Attach agent authZ + ATLAS routes (auth/token, mcp/call, atlas/*, ...) under `prefix`."""
    _ensure_package_paths()
    from atlas_enricher.router import build_router
    from identity_gateway.app import app as agent_app
    from identity_gateway.app import enricher, store

    if get_settings().agent_store == "supabase":
        from identity_gateway.supabase_repo import SupabaseAgentRepository

        from app.core.supabase import get_admin_client

        store.use_agent_repository(SupabaseAgentRepository(get_admin_client()))

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
