from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILES = (
    str(_BACKEND_DIR / ".env"),
    ".env",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults allow local agent-IAM demos without a filled .env; real Supabase
    # values are required for /api/v1/me and other user-auth routes.
    supabase_url: str = "https://example.supabase.co"
    supabase_anon_key: str = "dev-anon-key"
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""  # legacy HS256 projects only
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    # When true, agent management routes (/agents*, /auth/token, /audit) need a
    # Supabase user JWT. Off by default so the standalone demo console still works.
    agent_admin_requires_login: bool = False

    # Where agent definitions live. "memory" (default) is lost on restart;
    # "supabase" uses public.agents via the service role key.
    agent_store: Literal["memory", "supabase"] = "memory"

    # api.data.gov — gateway-held only; never returned to agents (AML.M0026).
    data_gov_api_key: str = ""
    # When empty, live mode auto-enables if data_gov_api_key is set.
    homefront_live_datagov: str = ""

    @field_validator("supabase_url")
    @classmethod
    def _normalize_url(cls, v: str) -> str:
        # Accept URLs copied from the dashboard with an API path, e.g. .../rest/v1/
        v = v.strip().rstrip("/")
        for suffix in ("/rest/v1", "/auth/v1"):
            if v.endswith(suffix):
                v = v[: -len(suffix)]
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def live_datagov(self) -> bool:
        flag = (self.homefront_live_datagov or "").strip().lower()
        if flag in {"1", "true", "yes", "on"}:
            return True
        if flag in {"0", "false", "no", "off"}:
            return False
        return bool(self.data_gov_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def apply_datagov_env() -> None:
    """Export gateway-held api.data.gov settings for MCP tool handlers."""
    import os

    settings = get_settings()
    if settings.data_gov_api_key:
        os.environ["DATA_GOV_API_KEY"] = settings.data_gov_api_key
    os.environ["HOMEFRONT_LIVE_DATAGOV"] = "1" if settings.live_datagov else "0"
