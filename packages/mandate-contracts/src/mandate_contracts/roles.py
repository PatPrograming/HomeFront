"""Government office roles (docs/ROLES.md): which tools each staffer's assistant may use.

The role lives in the STAFFER item in DynamoDB (`officeRole`), never in the token, the request body
or tool input. The gateway reads it on every call and sends it to Cedar as `principal.role`.

`ROLE_POLICIES` are Cedar `forbid`s, one per tool. A forbid always wins in Cedar, so a role can only
take tools away from the starter permits, never add one, and the starter forbids (bulk export,
campaign post) still bind every role, the Chief of Staff included. A missing or unknown role matches
no list, so every role forbid fires: fail closed.
"""

from __future__ import annotations

from typing import Literal

from .tools import TOOLS

Role = Literal["chief_of_staff", "caseworker", "communications_director", "legislative_aide", "intern"]

ROLE_LABELS: dict[str, str] = {
    "chief_of_staff": "Chief of Staff",
    "caseworker": "Caseworker",
    "communications_director": "Communications Director",
    "legislative_aide": "Legislative Aide",
    "intern": "Intern",
}

# Public research APIs (one input each, all untrusted output).
_RESEARCH = (
    "census_query",
    "openfda_search",
    "eia_query",
    "nasa_search",
    "noaa_climate",
    "nps_parks",
    "bls_series",
    "fec_search",
    "epa_search",
    "fcc_search",
)
_PROCUREMENT = ("usaspending_search", "sam_entity_lookup")

# The tools a role policy governs, in TOOLS order: every tool except the always-denied ones.
ROLE_GOVERNED_TOOLS = tuple(name for name, spec in TOOLS.items() if not spec.always_denied)

# The tools each role may use. records_bulk_export is in no list: forbid-bulk-export blocks it for everyone.
# The Agents tab reads this same table (role_allows), so the assistant and the agents share one cap.
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "chief_of_staff": ROLE_GOVERNED_TOOLS,
    "caseworker": ("inbox_read", "case_read", "datagov_search", "web_scrape", "email_send", *_RESEARCH, "report_save"),
    "communications_director": (
        "inbox_read",
        "datagov_search",
        "web_scrape",
        "email_send",
        "social_post",
        "social_reply",
        *_RESEARCH,
        "report_save",
    ),
    "legislative_aide": ("inbox_read", "datagov_search", "web_scrape", *_RESEARCH, *_PROCUREMENT, "report_save"),
    "intern": ("datagov_search", "web_scrape", "census_query", "nasa_search", "nps_parks", "noaa_climate"),
}

# What each tool does, for a staffer reading why their assistant was blocked.
TOOL_ACTIONS = {
    "inbox_read": "read the office inbox",
    "case_read": "open constituent case files",
    "datagov_search": "search public government data",
    "web_scrape": "read public web pages",
    "email_send": "send email for the office",
    "social_post": "post on the office's official account",
    "email_send_list": "email the whole office mailing list",
    "social_reply": "reply to comments on the office's official account",
    "usaspending_search": "search federal contract awards",
    "sam_entity_lookup": "look up federal vendors in SAM.gov",
    "report_save": "save reports for the office",
    "census_query": "look up Census data",
    "openfda_search": "search FDA drug, device and food data",
    "eia_query": "look up energy data",
    "nasa_search": "search NASA images",
    "noaa_climate": "look up NOAA climate data",
    "nps_parks": "look up National Park Service parks",
    "bls_series": "look up labor statistics",
    "fec_search": "search campaign finance records",
    "epa_search": "search EPA facility records",
    "fcc_search": "search FCC licenses",
}

# registry name ("email.send_list") -> mandate name ("email_send_list"). Only the first underscore is a dot.
_DOTTED_TO_MANDATE = {name.replace("_", ".", 1): name for name in TOOLS}


def mandate_tool_name(dotted: str) -> str:
    """The mandate tool for an exact registry name. Raises KeyError for case, spacing, unicode or underscore variants."""
    return _DOTTED_TO_MANDATE[dotted]


def role_allows(role: str | None, dotted: str) -> bool:
    """True if this office role may use the registry tool. A missing or unknown role is False (fail closed)."""
    return mandate_tool_name(dotted) in ROLE_TOOLS.get(role or "", ())


def role_policy_id(tool: str) -> str:
    return f"role-{tool.replace('_', '-')}"


def _role_policy(tool: str) -> str:
    roles = ", ".join(f'"{r}"' for r, tools in ROLE_TOOLS.items() if tool in tools)
    return f"""forbid(principal, action == Mandate::Action::"{tool}", resource)
  unless {{ principal has role && [{roles}].contains(principal.role) }};"""


ROLE_POLICIES: dict[str, str] = {role_policy_id(t): _role_policy(t) for t in ROLE_GOVERNED_TOOLS}


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", "No role")


def role_reason(role: str | None, tool: str) -> str:
    """Plain words for a role deny, e.g. "an Intern's assistant may not send email for the office"."""
    label = role_label(role)
    if label == "No role":
        return f"no office role is on file for this staffer, so the assistant may not {TOOL_ACTIONS.get(tool, tool)}"
    article = "an" if label[0] in "AEIOU" else "a"
    return f"{article} {label}'s assistant may not {TOOL_ACTIONS.get(tool, tool)}"
