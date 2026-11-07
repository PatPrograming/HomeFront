"""MCP tool contract (brief §6.1): names, input models and JSON schemas.

Tool names use underscores because Bedrock rejects dots in tool names
(`^[a-zA-Z0-9_-]{1,64}$`). Input models only describe shape. Every access
rule (same office, known recipient, official account) is enforced by Cedar
in the gateway, never here. That is why `social_post.account` is a plain
string: if the schema rejected "campaign", the agent's attempt would never
reach the gateway and the demo could not show the Cedar deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Input(BaseModel):
    # Unknown keys (for example an `office` the agent made up) are dropped, so the
    # gateway can only ever use the office from the token and DynamoDB (brief §10).
    model_config = ConfigDict(extra="ignore")


class InboxReadInput(_Input):
    email_id: str = Field(description="ID of the email to read, e.g. 'email-3'.")


class CaseReadInput(_Input):
    case_id: str = Field(description="ID of the constituent case, e.g. 'case-7'.")


class DatagovSearchInput(_Input):
    query: str = Field(description="Search terms for api.data.gov datasets.")


class WebScrapeInput(_Input):
    url: str = Field(description="Public URL to fetch.")


class EmailSendInput(_Input):
    to: str = Field(description="Recipient email address.")
    subject: str
    body: str


class SocialPostInput(_Input):
    account: str = Field(description="Which office account to post from, e.g. 'official'.")
    text: str


class RecordsBulkExportInput(_Input):
    query: str = Field(description="Which records to export.")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[_Input]
    untrusted_output: bool = False  # result goes through Guardrails scan()
    always_denied: bool = False  # a forbid policy always blocks it


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "inbox_read",
            "Read one email from the office's public inbox.",
            InboxReadInput,
            untrusted_output=True,
        ),
        ToolSpec("case_read", "Read a constituent case belonging to this office.", CaseReadInput),
        ToolSpec("datagov_search", "Search public datasets on api.data.gov.", DatagovSearchInput),
        ToolSpec(
            "web_scrape",
            "Fetch the text of a public web page.",
            WebScrapeInput,
            untrusted_output=True,
        ),
        ToolSpec(
            "email_send",
            "Send an email to a constituent on file or a .gov address.",
            EmailSendInput,
        ),
        ToolSpec("social_post", "Publish a post on the office's official account.", SocialPostInput),
        ToolSpec(
            "records_bulk_export",
            "Export constituent records in bulk.",
            RecordsBulkExportInput,
            always_denied=True,
        ),
    )
}


def input_schema(name: str) -> dict[str, Any]:
    return TOOLS[name].input_model.model_json_schema()


def tool_schemas() -> list[dict[str, Any]]:
    """MCP-style tool list: name, description, inputSchema."""
    return [
        {"name": t.name, "description": t.description, "inputSchema": input_schema(t.name)}
        for t in TOOLS.values()
    ]
