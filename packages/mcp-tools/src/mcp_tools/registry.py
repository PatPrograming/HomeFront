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


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "email.send": ToolDefinition(
        name="email.send",
        description="Send an outbound email on behalf of the agency.",
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0048"],
        atlas_mitigations=["AML.M0028", "AML.M0029", "AML.M0024"],
        requires_hitl=True,
    ),
    "social.post": ToolDefinition(
        name="social.post",
        description="Publish a social media post.",
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0048"],
        atlas_mitigations=["AML.M0028", "AML.M0029", "AML.M0024"],
        requires_hitl=True,
    ),
    "social.reply": ToolDefinition(
        name="social.reply",
        description="Reply to a social media comment or mention.",
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0051.001"],
        atlas_mitigations=["AML.M0028", "AML.M0030", "AML.M0024"],
        requires_hitl=True,
        untrusted_input=True,
    ),
    "web.scrape": ToolDefinition(
        name="web.scrape",
        description="Fetch and extract text from a public URL.",
        risk_level="medium",
        atlas_techniques=["AML.T0051.001"],
        atlas_mitigations=["AML.M0030", "AML.M0033", "AML.M0024"],
        untrusted_input=True,
    ),
    "datagov.search": ToolDefinition(
        name="datagov.search",
        description="Search datasets via api.data.gov (gateway holds X-Api-Key).",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
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
        # Explicitly never expose credential material.
        "credentials_visible_to_agent": False,
    }
