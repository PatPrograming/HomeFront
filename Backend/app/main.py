import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent_iam import ensure_package_paths, include_agent_iam
from app.core.config import apply_aws_env, apply_datagov_env, get_settings

ensure_package_paths()  # the routers below import the gateway packages

from app.routers import assistant, events, health, home, people, profiles, sessions, staff  # noqa: E402
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
        allow_headers=["Authorization", "Content-Type", "X-Agent-Token"],
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(home.router, prefix=API_PREFIX)
    app.include_router(profiles.router, prefix=API_PREFIX)

    # HomeFront broker and admin API (WP2)
    app.include_router(sessions.router, prefix=API_PREFIX)
    app.include_router(staff.router, prefix=API_PREFIX)
    app.include_router(people.router, prefix=API_PREFIX)  # admin: People & roles
    app.include_router(events.router, prefix=API_PREFIX)
    app.include_router(assistant.router, prefix=API_PREFIX)  # chat console, review queue, autonomy

    # Gateway: tool catalog, ATLAS, dashboard tool calls under /api/v1; MCP at /mcp (WP3)
    include_agent_iam(app, prefix=API_PREFIX)

    return app


app = create_app()
