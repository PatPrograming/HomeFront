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
    # Only says *which* email this answers. The gateway reads that email's kind from DynamoDB,
    # and only when `to` is its sender, to decide whether the reply is sent or held for review.
    in_reply_to: str | None = Field(default=None, description="ID of the inbox email this replies to, e.g. 'email-7'.")


class SocialPostInput(_Input):
    account: str = Field(description="Which office account to post from, e.g. 'official'.")
    text: str
    platform: str = Field(default="x", description="Platform to post on, e.g. 'x'.")


class EmailSendListInput(_Input):
    # No recipients field: the gateway reads the mailing list from DynamoDB, so the model cannot pick who gets it.
    subject: str
    body: str


class SocialReplyInput(_Input):
    account: str = Field(description="Which office account to reply from, e.g. 'official'.")
    reply_to: str = Field(description="Id or URL of the post or comment to reply to.")
    text: str
    platform: str = Field(default="x", description="Platform the comment is on, e.g. 'x'.")


class UsaspendingSearchInput(_Input):
    keywords: str = Field(description="Keywords describing the product or service, e.g. 'body worn cameras'.")
    fiscal_year: int = Field(description="Federal fiscal year to search, e.g. 2025. Use 0 for the last five.")


class SamEntityLookupInput(_Input):
    name: str = Field(description="Legal business name, or part of it, to search for.")


class ReportSaveInput(_Input):
    title: str = Field(description="Report title (up to 200 characters).")
    body: str = Field(description="Report body in markdown.")


class CensusQueryInput(_Input):
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)  # the model may send year as 2023
    year: str = Field(description="Vintage year, e.g. '2023'.")
    dataset: str = Field(description="Path after year, e.g. 'acs/acs1' or 'pep/population'.")
    get: str = Field(description="Comma-separated variables, e.g. 'NAME,B01003_001E'.")
    for_: str = Field(alias="for", description="Geography for= clause, e.g. 'place:69084'.")
    in_: str = Field(alias="in", description="Optional in= clause, e.g. 'state:06'. Use '' if none.")


class OpenfdaSearchInput(_Input):
    endpoint: str = Field(description="API path without .json, e.g. 'drug/event' or 'drug/label'.")
    search: str = Field(description="openFDA search expression.")
    limit: str = Field(description="Max results 1-20 as a string, e.g. '5'.")


class EiaQueryInput(_Input):
    route: str = Field(description="EIA v2 route without /data, e.g. 'petroleum/crd/crpdn'.")
    frequency: str = Field(description="e.g. 'monthly' or 'annual'.")
    length: str = Field(description="Number of rows to return, e.g. '5'.")
    facet: str = Field(description="Optional facet as name:value, e.g. 'respondent:CISO'. Use '' if none.")


class NasaSearchInput(_Input):
    q: str = Field(description="Search keywords, e.g. 'mars rover'.")


class NoaaClimateInput(_Input):
    datasetid: str = Field(description="e.g. 'GHCND'.")
    locationid: str = Field(description="e.g. 'ZIP:95050' or 'FIPS:06'.")
    startdate: str = Field(description="YYYY-MM-DD")
    enddate: str = Field(description="YYYY-MM-DD")
    datatypeid: str = Field(description="Optional datatype, e.g. 'TMAX'. Use '' if none.")


class NpsParksInput(_Input):
    q: str = Field(description="Search text, e.g. 'yosemite'. Use '' if using parkCode only.")
    park_code: str = Field(alias="parkCode", description="Optional park code, e.g. 'yose'. Use '' if none.")


class BlsSeriesInput(_Input):
    series_id: str = Field(description="BLS series id, e.g. 'CUUR0000SA0' (CPI-U).")
    startyear: str = Field(description="Start year YYYY.")
    endyear: str = Field(description="End year YYYY.")


class FecSearchInput(_Input):
    q: str = Field(description="Candidate name search.")


class EpaSearchInput(_Input):
    q: str = Field(description="City or facility search text, e.g. 'SANTA CLARA'.")


class FccSearchInput(_Input):
    q: str = Field(description="Licensee or callsign search.")


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
            "email_send_list",
            "Email everyone on the office's mailing list. Always held for staff review.",
            EmailSendListInput,
        ),
        ToolSpec("social_reply", "Reply to a comment or mention from the office's official account.", SocialReplyInput),
        ToolSpec(
            "usaspending_search",
            "Search past federal contract awards on USAspending.gov.",
            UsaspendingSearchInput,
            untrusted_output=True,
        ),
        ToolSpec(
            "sam_entity_lookup",
            "Look up vendors registered in SAM.gov by business name.",
            SamEntityLookupInput,
            untrusted_output=True,
        ),
        ToolSpec("report_save", "Save a finished markdown report for the office staff.", ReportSaveInput),
        ToolSpec("census_query", "Query the U.S. Census Data API.", CensusQueryInput, untrusted_output=True),
        ToolSpec("openfda_search", "Search openFDA (drugs, devices, food).", OpenfdaSearchInput, untrusted_output=True),
        ToolSpec("eia_query", "Query U.S. Energy Information Administration open data.", EiaQueryInput, untrusted_output=True),
        ToolSpec("nasa_search", "Search the NASA image library.", NasaSearchInput, untrusted_output=True),
        ToolSpec("noaa_climate", "Fetch NOAA Climate Data Online observations.", NoaaClimateInput, untrusted_output=True),
        ToolSpec("nps_parks", "Search National Park Service parks.", NpsParksInput, untrusted_output=True),
        ToolSpec("bls_series", "Fetch BLS time series (CPI, employment, etc.).", BlsSeriesInput, untrusted_output=True),
        ToolSpec("fec_search", "Search FEC candidates (campaign finance).", FecSearchInput, untrusted_output=True),
        ToolSpec("epa_search", "Search EPA Envirofacts facilities.", EpaSearchInput, untrusted_output=True),
        ToolSpec("fcc_search", "Search FCC licenses.", FccSearchInput, untrusted_output=True),
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
