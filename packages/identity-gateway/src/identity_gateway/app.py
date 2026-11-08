"""Gateway HTTP routes: tool catalog, ATLAS strip, and the dashboard's tool-call path.

Agents use the real MCP endpoint (`mcp_server.py`, at `/mcp`). `POST /mcp/call`
is kept only for dashboard buttons, and it goes through the same `guarded()`.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.routing import Route

# Allow running without editable installs by putting sibling packages on path.
_PACKAGES = Path(__file__).resolve().parents[3]
for _rel in ("atlas-enricher/src", "mcp-tools/src", "mandate-contracts/src"):
    if (_PACKAGES / _rel).is_dir() and str(_PACKAGES / _rel) not in sys.path:
        sys.path.insert(0, str(_PACKAGES / _rel))

from atlas_enricher import AtlasEnricher  # noqa: E402
from atlas_enricher.router import build_router  # noqa: E402

from mandate_contracts import tool_schemas  # noqa: E402

from .gateway import get_gateway  # noqa: E402
from .mcp_server import PATH as MCP_PATH  # noqa: E402
from .mcp_server import endpoint as mcp_endpoint  # noqa: E402
from .tokens import InvalidToken  # noqa: E402

enricher = AtlasEnricher()
router = APIRouter()


class ToolCallRequest(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("/tools")
def tools_catalog() -> list[dict[str, Any]]:
    """The seven HomeFront tools with their input schemas."""
    return tool_schemas()


@router.post("/mcp/call")
def mcp_call(body: ToolCallRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Dashboard path: same `guarded()` as MCP. 401 on a bad token, else the decision."""
    token = authorization[len("Bearer ") :].strip() if authorization and authorization.startswith("Bearer ") else ""
    try:
        outcome = get_gateway().guarded(body.tool, body.arguments, token)
    except InvalidToken:
        raise HTTPException(status_code=401, detail="Invalid or missing mandate token")
    return {
        "decision": outcome.event.decision,
        "event": outcome.event.model_dump(mode="json"),
        "result": outcome.result,
        "message": None if outcome.allowed else outcome.deny_text(),
    }


def _atlas_card(entry: dict[str, Any] | None, oid: str) -> dict[str, Any]:
    """id / name / description only — enough for UI hover tooltips without the full pack."""
    if not entry:
        return {"id": oid, "name": None, "description": None}
    return {
        "id": entry.get("id") or oid,
        "name": entry.get("name"),
        "description": entry.get("description"),
    }


def _matrix_atlas_ids() -> tuple[list[str], list[str]]:
    """Technique and mitigation IDs the UI may show (Prompt 1 + every MCP tool)."""
    from mcp_tools.registry import TOOL_REGISTRY

    techniques = {"AML.T0053", "AML.T0051.001", "AML.T0086", "AML.T0098"}
    mitigations = {"AML.M0024", "AML.M0028", "AML.M0029", "AML.M0030", "AML.M0026", "AML.M0033"}
    for tool in TOOL_REGISTRY.values():
        techniques.update(tool.atlas_techniques or [])
        mitigations.update(tool.atlas_mitigations or [])
    return sorted(techniques), sorted(mitigations)


@router.get("/demo/matrix")
def demo_matrix() -> dict[str, Any]:
    """ATLAS cards for Prompt 1 and tool-linked AML IDs (name + full description for hover)."""
    technique_ids, mitigation_ids = _matrix_atlas_ids()
    return {
        "atlas_version": enricher.version,
        "techniques": [_atlas_card(enricher.get_technique(i), i) for i in technique_ids],
        "mitigations": [_atlas_card(enricher.get_mitigation(i), i) for i in mitigation_ids],
    }


def include_gateway(app: FastAPI, prefix: str = "") -> None:
    """Attach the gateway to an app: REST routes and ATLAS under `prefix`, MCP at `/mcp`."""
    app.include_router(build_router(enricher), prefix=prefix)
    app.include_router(router, prefix=prefix)
    app.router.routes.append(Route(MCP_PATH, endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]))


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with mcp_endpoint.lifespan():
        yield


# Standalone gateway (`python -m identity_gateway`); the unified app is Backend/app/main.py.
app = FastAPI(title="HomeFront Gateway", version="0.2.0", lifespan=lifespan)
include_gateway(app)
