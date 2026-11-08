from functools import lru_cache
from pathlib import Path
from typing import Literal

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

    allowed_origins: str = "http://localhost:3000"

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
    aws_profile: str = ""  # exported by apply_aws_env() for boto3 (SES), if set in .env
    dynamodb_table_prefix: str = "homefront-"
    dynamodb_endpoint_url: str = ""  # http://localhost:8000 for DynamoDB Local

    # Teammates' agent store (identity_gateway.agents_api). "memory" (default) is
    # lost on restart; "dynamodb" uses the <prefix>agents/audit/approvals tables.
    agent_store: Literal["memory", "dynamodb"] = "memory"

    # Teammates' agent runner (Run a task): Claude on Amazon Bedrock. Region enables
    # it (falls back to AWS_REGION); empty = POST /agents/{id}/runs returns 503.
    bedrock_region: str = ""
    aws_bearer_token_bedrock: str = ""
    bedrock_model_id: str = "anthropic.claude-opus-5-5"
    # Retried automatically if the primary model declines (refusal) a request.
    bedrock_fallback_model_id: str = "anthropic.claude-opus-4-8"
    runner_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    # Outbound email (SES). Fallback sender when an agent has none of its own.
    homefront_email_from: str = ""
    homefront_ses_region: str = ""

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
    def resolved_bedrock_region(self) -> str:
        return self.bedrock_region.strip() or self.resolved_aws_region

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


def apply_aws_env() -> None:
    """Export AWS and email settings from .env for code that reads os.environ
    (boto3's credential chain, the MCP email handlers). Values already set in
    the real environment win."""
    import os

    settings = get_settings()
    exports = {
        "AWS_PROFILE": settings.aws_profile,
        "AWS_REGION": settings.resolved_aws_region,
        "AWS_DEFAULT_REGION": settings.resolved_aws_region,
        "HOMEFRONT_EMAIL_FROM": settings.homefront_email_from,
        "HOMEFRONT_SES_REGION": settings.homefront_ses_region,
    }
    for key, value in exports.items():
        if value and value.strip():
            os.environ.setdefault(key, value.strip())


def apply_datagov_env() -> None:
    """Export gateway-held api.data.gov settings for MCP tool handlers."""
    import os

    settings = get_settings()
    if settings.data_gov_api_key:
        os.environ["DATA_GOV_API_KEY"] = settings.data_gov_api_key
    os.environ["HOMEFRONT_LIVE_DATAGOV"] = "1" if settings.live_datagov else "0"
