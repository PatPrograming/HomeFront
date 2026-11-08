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

    # Region for Cognito-independent AWS calls (DynamoDB, and Bedrock unless
    # BEDROCK_REGION overrides). Falls back to AWS_REGION / AWS_DEFAULT_REGION.
    aws_region: str = ""
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    # --- User auth: Amazon Cognito ------------------------------------------
    # From the CloudFormation stack outputs. Empty = user-auth routes return 503
    # (the agent-IAM demo still works without them).
    cognito_user_pool_id: str = ""  # e.g. us-east-1_AbC123xyz
    cognito_app_client_id: str = ""

    # When true, agent management routes (/agents*, /auth/token, /audit) need a
    # signed-in Cognito user. Off by default so the standalone demo console works.
    agent_admin_requires_login: bool = False

    # --- Data: Amazon DynamoDB ----------------------------------------------
    # Table names are <prefix><name>: profiles, usernames, agents.
    dynamodb_table_prefix: str = "homefront-"
    # Set to http://localhost:8000 for DynamoDB Local; empty = real AWS.
    dynamodb_endpoint_url: str = ""
    # Where agent definitions live. "memory" (default) is lost on restart;
    # "dynamodb" uses the <prefix>agents table.
    agent_store: Literal["memory", "dynamodb"] = "memory"

    # --- Agent runner: Claude on Amazon Bedrock ---------------------------
    # Region enables the runner; empty = POST /agents/{id}/runs returns 503.
    # Falls back to AWS_REGION when unset.
    bedrock_region: str = ""
    # Auth: a Bedrock API key, else the standard AWS credential chain (env
    # keys, AWS_PROFILE, SSO, instance role) signed with SigV4.
    aws_bearer_token_bedrock: str = ""
    aws_profile: str = ""
    bedrock_model_id: str = "anthropic.claude-opus-5-5"
    # Retried automatically if the primary model declines (refusal) a request.
    bedrock_fallback_model_id: str = "anthropic.claude-opus-4-8"
    runner_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"

    # api.data.gov — gateway-held only; never returned to agents (AML.M0026).
    data_gov_api_key: str = ""
    # When empty, live mode auto-enables if data_gov_api_key is set.
    homefront_live_datagov: str = ""

    @field_validator("cognito_user_pool_id", "cognito_app_client_id", "dynamodb_endpoint_url")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @property
    def cognito_configured(self) -> bool:
        return bool(self.cognito_user_pool_id and self.cognito_app_client_id)

    @property
    def cognito_region(self) -> str:
        # Pool ids are "<region>_<id>".
        return self.cognito_user_pool_id.split("_", 1)[0]

    @property
    def cognito_issuer(self) -> str:
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"
        )

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
    def resolved_bedrock_region(self) -> str:
        return self.bedrock_region.strip() or self.resolved_aws_region

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
