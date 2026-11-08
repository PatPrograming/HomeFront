from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    risk_level: str
    atlas_techniques: list[str]
    atlas_mitigations: list[str]
    requires_hitl: bool = False
    holds_secrets: bool = False
    untrusted_input: bool = False
    # JSON Schema for the tool's arguments, as offered to the model. Every
    # property is required and no extras are allowed, so it works with strict
    # tool use (schema-valid arguments guaranteed).
    input_schema: dict[str, Any] = field(default_factory=dict)


def _schema(**props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "email.send": ToolDefinition(
        name="email.send",
        description="Send an outbound email on behalf of the agency.",
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0048"],
        atlas_mitigations=["AML.M0028", "AML.M0029", "AML.M0024"],
        requires_hitl=True,
        input_schema=_schema(
            to={"type": "string", "description": "Recipient email address."},
            subject={"type": "string", "description": "Subject line."},
            body={"type": "string", "description": "Plain-text email body."},
        ),
    ),
    "social.post": ToolDefinition(
        name="social.post",
        description="Publish a social media post.",
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0048"],
        atlas_mitigations=["AML.M0028", "AML.M0029", "AML.M0024"],
        requires_hitl=True,
        input_schema=_schema(
            platform={"type": "string", "description": "Platform, e.g. 'x' or 'facebook'."},
            text={"type": "string", "description": "Post text."},
        ),
    ),
    "social.reply": ToolDefinition(
        name="social.reply",
        description="Reply to a social media comment or mention.",
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0051.001"],
        atlas_mitigations=["AML.M0028", "AML.M0030", "AML.M0024"],
        requires_hitl=True,
        untrusted_input=True,
        input_schema=_schema(
            platform={"type": "string", "description": "Platform, e.g. 'x' or 'facebook'."},
            reply_to={"type": "string", "description": "Id of the post or comment to reply to."},
            text={"type": "string", "description": "Reply text."},
        ),
    ),
    "web.scrape": ToolDefinition(
        name="web.scrape",
        description="Fetch and extract text from a public URL.",
        risk_level="medium",
        atlas_techniques=["AML.T0051.001"],
        atlas_mitigations=["AML.M0030", "AML.M0033", "AML.M0024"],
        untrusted_input=True,
        input_schema=_schema(
            url={"type": "string", "description": "Public http(s) URL to fetch."},
        ),
    ),
    "datagov.search": ToolDefinition(
        name="datagov.search",
        description="Search datasets via api.data.gov (gateway holds X-Api-Key).",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            q={"type": "string", "description": "Search keywords for data.gov datasets."},
        ),
    ),
}


def list_tools() -> list[ToolDefinition]:
    return list(TOOL_REGISTRY.values())


def get_tool(name: str) -> ToolDefinition | None:
    return TOOL_REGISTRY.get(name)


def tool_public_view(tool: ToolDefinition) -> dict[str, Any]:
    """Return tool metadata safe to expose to agents (no secrets)."""
    return {
        "name": tool.name,
        "description": tool.description,
        "risk_level": tool.risk_level,
        "atlas_techniques": tool.atlas_techniques,
        "atlas_mitigations": tool.atlas_mitigations,
        "requires_hitl": tool.requires_hitl,
        "holds_secrets": tool.holds_secrets,
        "untrusted_input": tool.untrusted_input,
        "input_schema": tool.input_schema,
        # Explicitly never expose credential material.
        "credentials_visible_to_agent": False,
    }
