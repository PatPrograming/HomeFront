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
    # Sends data outside the agency (email, social). Denied in a run or session
    # that has already read untrusted content (taint tracking, AML.M0030).
    outbound: bool = False
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
        outbound=True,
        input_schema=_schema(
            to={"type": "string", "description": "Recipient email address."},
            subject={"type": "string", "description": "Subject line."},
            body={"type": "string", "description": "Plain-text email body."},
        ),
    ),
    "email.send_list": ToolDefinition(
        name="email.send_list",
        description=(
            "Send one email to every address on this agent's mailing list. Each "
            "recipient gets their own copy and can't see the others."
        ),
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0048"],
        atlas_mitigations=["AML.M0028", "AML.M0029", "AML.M0024"],
        requires_hitl=True,
        outbound=True,
        input_schema=_schema(
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
        outbound=True,
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
        outbound=True,
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
    "usaspending.search": ToolDefinition(
        name="usaspending.search",
        description=(
            "Search past federal contract awards on USAspending.gov: who won, which "
            "agency awarded it, amounts and dates. Results are external data."
        ),
        risk_level="medium",
        atlas_techniques=["AML.T0051.001", "AML.T0099"],
        atlas_mitigations=["AML.M0030", "AML.M0033", "AML.M0024"],
        untrusted_input=True,
        input_schema=_schema(
            keywords={
                "type": "string",
                "description": "Keywords describing the product or service, e.g. 'body worn cameras'.",
            },
            fiscal_year={
                "type": "integer",
                "description": "Federal fiscal year to search, e.g. 2025. Use 0 for the last five fiscal years.",
            },
        ),
    ),
    "sam.entity_lookup": ToolDefinition(
        name="sam.entity_lookup",
        description=(
            "Look up vendors registered in SAM.gov by business name: UEI, CAGE code, "
            "registration status and small-business types (gateway holds the API key)."
        ),
        risk_level="medium",
        atlas_techniques=["AML.T0098", "AML.T0051.001"],
        atlas_mitigations=["AML.M0026", "AML.M0030", "AML.M0024"],
        holds_secrets=True,
        # Registration text is written by the vendors themselves.
        untrusted_input=True,
        input_schema=_schema(
            name={"type": "string", "description": "Legal business name, or part of it, to search for."},
        ),
    ),
    "report.save": ToolDefinition(
        name="report.save",
        description=(
            "Save a finished markdown report for the agency staff who assigned the task. "
            "Saved reports are listed on the agent; nothing is sent anywhere."
        ),
        risk_level="low",
        atlas_techniques=["AML.T0067.000"],
        atlas_mitigations=["AML.M0033", "AML.M0024"],
        input_schema=_schema(
            title={"type": "string", "description": "Report title (up to 200 characters)."},
            body={"type": "string", "description": "Report body in markdown."},
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
        "outbound": tool.outbound,
        "input_schema": tool.input_schema,
        # Explicitly never expose credential material.
        "credentials_visible_to_agent": False,
    }
