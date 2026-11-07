"""Mount MITRE ATLAS agent IAM routes onto the HomeFront API.

User auth stays on Supabase (`app.core.auth`). Agent workload identity,
MCP tool grants/revoke, and ATLAS enrichment live in `packages/identity-gateway`
and are attached here so one FastAPI process serves both.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_SRC = [
    _REPO_ROOT / "packages" / "atlas-enricher" / "src",
    _REPO_ROOT / "packages" / "mcp-tools" / "src",
    _REPO_ROOT / "packages" / "identity-gateway" / "src",
]


def _ensure_package_paths() -> None:
    for path in _PACKAGE_SRC:
        resolved = str(path)
        if path.is_dir() and resolved not in sys.path:
            sys.path.insert(0, resolved)


def include_agent_iam(host_app: FastAPI) -> None:
    """Attach agent authZ + ATLAS routes (auth/token, mcp/call, atlas/*, …)."""
    _ensure_package_paths()
    from atlas_enricher.router import build_router
    from identity_gateway.app import app as agent_app
    from identity_gateway.app import enricher

    # Nested include_router objects don't remount correctly if only appended;
    # attach the ATLAS router directly to the host app.
    host_app.include_router(build_router(enricher))

    for route in agent_app.routes:
        if not isinstance(route, APIRoute):
            continue
        host_app.routes.append(route)
