"""Where the agent connects and which model it uses. Everything comes from env vars or CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080/mcp"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-5"
DEFAULT_REGION = "us-east-1"


@dataclass(frozen=True)
class Settings:
    gateway_url: str  # the gateway's MCP endpoint
    api_url: str  # the gateway's HTTP API root (broker lives at /api/v1/...)
    model_id: str
    region: str

    @classmethod
    def from_env(cls, gateway_url: str | None = None) -> Settings:
        gateway = gateway_url or os.environ.get("MANDATE_GATEWAY_URL", DEFAULT_GATEWAY_URL)
        api = os.environ.get("MANDATE_API_URL") or gateway.removesuffix("/").removesuffix("/mcp")
        return cls(
            gateway_url=gateway,
            api_url=api,
            model_id=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
            region=os.environ.get("AWS_REGION", DEFAULT_REGION),
        )
