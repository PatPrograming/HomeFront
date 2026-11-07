from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""  # legacy HS256 projects only
    allowed_origins: str = "http://localhost:3000"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
