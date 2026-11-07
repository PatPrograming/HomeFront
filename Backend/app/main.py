from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent_iam import include_agent_iam
from app.core.config import get_settings
from app.routers import health, profiles

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="HomeFront API",
        description=(
            "Supabase user auth + agent workload identity with MITRE ATLAS-tagged MCP authorization"
        ),
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(profiles.router, prefix=API_PREFIX)

    # Prompt 1: agent identity, tool grants/revoke, ATLAS enrichment
    include_agent_iam(app)

    return app


app = create_app()
