"""Mount the HomeFront gateway onto the HomeFront API.

The gateway lives in `packages/identity-gateway`. This module puts its packages
on the import path and attaches it to the host app:

- `/api/v1/tools`, `/api/v1/atlas/*`, `/api/v1/demo/matrix`: public catalogs.
- `/api/v1/mcp/call`: dashboard tool calls, authenticated by a mandate token.
- `/mcp`: the real MCP endpoint for the agent, authenticated by a mandate token.

There is no route that issues an agent token on its own: mandate tokens come
only from the broker (`routers/sessions.py`), which needs a staffer login and
the agent's own client credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_SRC = [
    _REPO_ROOT / "packages" / "mandate-contracts" / "src",
    _REPO_ROOT / "packages" / "atlas-enricher" / "src",
    _REPO_ROOT / "packages" / "mcp-tools" / "src",
    _REPO_ROOT / "packages" / "identity-gateway" / "src",
]


def ensure_package_paths() -> None:
    for path in _PACKAGE_SRC:
        resolved = str(path)
        if path.is_dir() and resolved not in sys.path:
            sys.path.insert(0, resolved)


def include_agent_iam(host_app: FastAPI, prefix: str = "") -> None:
    """Attach the gateway routes under `prefix` and the MCP endpoint at `/mcp`."""
    ensure_package_paths()
    from identity_gateway.app import include_gateway

    include_gateway(host_app, prefix=prefix)
