import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.agent_iam import ensure_package_paths, include_agent_iam
from app.core.config import apply_aws_env, apply_datagov_env, get_settings

ensure_package_paths()  # the routers below import the gateway packages

from app.routers import assistant, connections, events, health, home, oauth, people, profiles, sessions, staff  # noqa: E402
from identity_gateway.app import lifespan as gateway_lifespan  # noqa: E402

from app.services.agent_supervisor import supervision  # noqa: E402

API_PREFIX = "/api/v1"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """The MCP endpoint's session manager, and the chat agents the console started (stopped on shutdown)."""
    async with gateway_lifespan(app), supervision():
        yield


def create_app() -> FastAPI:
    settings = get_settings()
    apply_datagov_env()
    apply_aws_env()
    app = FastAPI(
        title="HomeFront API",
        description="HomeFront: delegated agent identity, Cedar-authorized MCP tools, instant offboarding",
        version="0.2.0",
        lifespan=lifespan,  # starts the MCP endpoint's session manager
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Agent-Token",
            "Signature",
            "Signature-Key",
            "Content-Digest",
            "X-Auth-Protocol",
        ],
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(home.router, prefix=API_PREFIX)
    app.include_router(profiles.router, prefix=API_PREFIX)
    app.include_router(connections.router, prefix=API_PREFIX)

    # HomeFront broker and admin API (WP2)
    app.include_router(sessions.router, prefix=API_PREFIX)
    app.include_router(oauth.router, prefix=API_PREFIX)
    app.include_router(staff.router, prefix=API_PREFIX)
    app.include_router(people.router, prefix=API_PREFIX)  # admin: People & roles
    app.include_router(events.router, prefix=API_PREFIX)
    app.include_router(assistant.router, prefix=API_PREFIX)  # chat console, review queue, autonomy

    # Gateway: tool catalog, ATLAS, dashboard tool calls under /api/v1; MCP at /mcp (WP3)
    include_agent_iam(app, prefix=API_PREFIX)

    # Deployed (deploy/apprunner): the same server also serves the built console, so the
    # browser, /api/v1 and /mcp share one origin. Unset in local dev (Vite serves :3000).
    frontend_dist = os.environ.get("FRONTEND_DIST", "").strip()
    if frontend_dist:
        mount_frontend(app, Path(frontend_dist))

    return app


def mount_frontend(app: FastAPI, dist: Path) -> None:
    """Serve the Vite build: real files as-is, every other GET path gets index.html (client routing)."""
    dist = dist.resolve()
    index = dist / "index.html"
    if not index.is_file():
        raise RuntimeError(f"FRONTEND_DIST={dist} has no index.html (run `npm run build` in Frontend/)")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        # Registered last, so real routes win; unknown API paths still 404 instead of returning HTML.
        if path == "mcp" or path.startswith(("api/", "mcp/")):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(dist):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
