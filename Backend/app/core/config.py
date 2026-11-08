from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent
_ENV_FILES = (
    str(_REPO_ROOT / ".env"),
    str(_BACKEND_DIR / ".env"),  # legacy fallback
    ".env",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    # Amazon Cognito (printed by Backend/infra/cognito.sh). Empty ids mean every
    # token is rejected: the backend fails closed until the pool is configured.
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str = ""
    cognito_staff_client_id: str = ""
    cognito_agent_client_id: str = ""
    cognito_domain: str = ""

    # Teammates' alternative stack only (infra/cloudformation/homefront.yaml,
    # scripts/create_tables.py, app/core/dynamodb.py). The running app uses the
    # `mandate` table, not these.
    aws_region: str = ""
    dynamodb_table_prefix: str = "homefront-"
    dynamodb_endpoint_url: str = ""  # http://localhost:8000 for DynamoDB Local

    # api.data.gov — gateway-held only; never returned to agents (AML.M0026).
    data_gov_api_key: str = ""
    # When empty, live mode auto-enables if data_gov_api_key is set.
    homefront_live_datagov: str = ""

    @field_validator("dynamodb_endpoint_url")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def resolved_aws_region(self) -> str:
        import os

        return (
            self.aws_region.strip()
            or os.environ.get("AWS_REGION", "").strip()
            or os.environ.get("AWS_DEFAULT_REGION", "").strip()
        )

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
