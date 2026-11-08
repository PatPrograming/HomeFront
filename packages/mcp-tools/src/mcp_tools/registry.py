from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .share_links import SUPPORTED as SHARE_PLATFORM_IDS


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
        description=(
            "Draft a social media post. After a person approves it, they get a prefilled "
            "compose link and press Post themselves; nothing is published automatically."
        ),
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0048"],
        atlas_mitigations=["AML.M0028", "AML.M0029", "AML.M0024"],
        requires_hitl=True,
        outbound=True,
        input_schema=_schema(
            platform={
                "type": "string",
                "enum": list(SHARE_PLATFORM_IDS),
                "description": "Platform to post on.",
            },
            text={
                "type": "string",
                "description": (
                    "Post text. Put the official link in it. Facebook and LinkedIn share only "
                    "the link, so include one for those. Keep within the platform's limit "
                    "(X 280, Bluesky 300)."
                ),
            },
        ),
    ),
    "social.reply": ToolDefinition(
        name="social.reply",
        description=(
            "Draft a reply to a social media comment or mention. After a person approves it, "
            "they get a prefilled compose link and post it themselves."
        ),
        risk_level="high",
        atlas_techniques=["AML.T0086", "AML.T0051.001"],
        atlas_mitigations=["AML.M0028", "AML.M0030", "AML.M0024"],
        requires_hitl=True,
        outbound=True,
        untrusted_input=True,
        input_schema=_schema(
            platform={
                "type": "string",
                "enum": list(SHARE_PLATFORM_IDS),
                "description": "Platform the comment is on.",
            },
            reply_to={"type": "string", "description": "Id or URL of the post or comment to reply to."},
            text={"type": "string", "description": "Reply text."},
        ),
    ),
    "web.scrape": ToolDefinition(
        name="web.scrape",
        description=(
            "Fetch and extract text from a public URL. For large CSVs, pass q with the "
            "place/name to keep matching rows (e.g. 'Santa Clara city'). Prefer "
            "www2.census.gov CSV population files or api.census.gov over QuickFacts HTML."
        ),
        risk_level="medium",
        atlas_techniques=["AML.T0051.001"],
        atlas_mitigations=["AML.M0030", "AML.M0033", "AML.M0024"],
        untrusted_input=True,
        input_schema=_schema(
            url={"type": "string", "description": "Public http(s) URL to fetch."},
            q={
                "type": "string",
                "description": (
                    "Optional focus keywords for large files (city/place name). "
                    "Use '' if not needed."
                ),
            },
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
    "census.query": ToolDefinition(
        name="census.query",
        description=(
            "Query the U.S. Census Data API (ACS, population estimates, etc.). "
            "Gateway holds CENSUS_API_KEY. Example: get=NAME,B01003_001E for=place:69084 in=state:06."
        ),
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            year={"type": "string", "description": "Vintage year, e.g. '2023'."},
            dataset={"type": "string", "description": "Path after year, e.g. 'acs/acs1' or 'pep/population'."},
            get={"type": "string", "description": "Comma-separated variables, e.g. 'NAME,B01003_001E'."},
            **{
                "for": {
                    "type": "string",
                    "description": "Geography for= clause, e.g. 'place:69084' or 'place:*'.",
                },
                "in": {
                    "type": "string",
                    "description": "Optional in= clause, e.g. 'state:06'. Use '' if none.",
                },
            },
        ),
    ),
    "openfda.search": ToolDefinition(
        name="openfda.search",
        description="Search openFDA (drugs, devices, food). Gateway holds OPENFDA_API_KEY.",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            endpoint={
                "type": "string",
                "description": "API path without .json, e.g. 'drug/event' or 'drug/label'.",
            },
            search={"type": "string", "description": "openFDA search expression."},
            limit={"type": "string", "description": "Max results 1-20 as a string, e.g. '5'."},
        ),
    ),
    "eia.query": ToolDefinition(
        name="eia.query",
        description="Query U.S. Energy Information Administration open data. Gateway holds EIA_API_KEY.",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            route={
                "type": "string",
                "description": "EIA v2 route without /data, e.g. 'petroleum/crd/crpdn'.",
            },
            frequency={"type": "string", "description": "e.g. 'monthly' or 'annual'."},
            length={"type": "string", "description": "Number of rows to return, e.g. '5'."},
            facet={
                "type": "string",
                "description": "Optional facet as name:value, e.g. 'respondent:CISO'. Use '' if none.",
            },
        ),
    ),
    "nasa.search": ToolDefinition(
        name="nasa.search",
        description="Search NASA image library (and uses NASA_API_KEY when configured).",
        risk_level="low",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            q={"type": "string", "description": "Search keywords, e.g. 'mars rover'."},
        ),
    ),
    "noaa.climate": ToolDefinition(
        name="noaa.climate",
        description="NOAA Climate Data Online observations. Gateway holds NOAA_CDO_TOKEN.",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            datasetid={"type": "string", "description": "e.g. 'GHCND'."},
            locationid={"type": "string", "description": "e.g. 'ZIP:95050' or 'FIPS:06' or 'CITY:US060013'."},
            startdate={"type": "string", "description": "YYYY-MM-DD"},
            enddate={"type": "string", "description": "YYYY-MM-DD"},
            datatypeid={
                "type": "string",
                "description": "Optional datatype, e.g. 'TMAX'. Use '' if none.",
            },
        ),
    ),
    "nps.parks": ToolDefinition(
        name="nps.parks",
        description="Search National Park Service parks. Gateway holds NPS_API_KEY.",
        risk_level="low",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            q={"type": "string", "description": "Search text, e.g. 'yosemite'. Use '' if using parkCode only."},
            parkCode={"type": "string", "description": "Optional park code, e.g. 'yose'. Use '' if none."},
        ),
    ),
    "bls.series": ToolDefinition(
        name="bls.series",
        description="Fetch BLS time series (CPI, employment, etc.). Gateway holds BLS_API_KEY.",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            series_id={"type": "string", "description": "BLS series id, e.g. 'CUUR0000SA0' (CPI-U)."},
            startyear={"type": "string", "description": "Start year YYYY."},
            endyear={"type": "string", "description": "End year YYYY."},
        ),
    ),
    "fec.search": ToolDefinition(
        name="fec.search",
        description="Search FEC candidates (campaign finance). Uses FEC_API_KEY or DATA_GOV_API_KEY.",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            q={"type": "string", "description": "Candidate name search."},
        ),
    ),
    "epa.search": ToolDefinition(
        name="epa.search",
        description="Search EPA Envirofacts facilities by city/name. Uses EPA_API_KEY when set.",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            q={"type": "string", "description": "City or facility search text, e.g. 'SANTA CLARA'."},
        ),
    ),
    "fcc.search": ToolDefinition(
        name="fcc.search",
        description="Search FCC licenses. Uses FCC_API_KEY or DATA_GOV_API_KEY.",
        risk_level="medium",
        atlas_techniques=["AML.T0098"],
        atlas_mitigations=["AML.M0026", "AML.M0024"],
        holds_secrets=True,
        input_schema=_schema(
            q={"type": "string", "description": "Licensee or callsign search."},
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
