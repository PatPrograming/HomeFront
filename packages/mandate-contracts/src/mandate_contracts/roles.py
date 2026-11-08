"""Government office roles (docs/ROLES.md): which tools each staffer's assistant may use.

The role lives in the STAFFER item in DynamoDB (`officeRole`), never in the token, the request body
or tool input. The gateway reads it on every call and sends it to Cedar as `principal.role`.

What each role may do is data, not policy: `ROLE_TOOLS` is the reviewed default, and an admin can
replace a role's list in DynamoDB (`ROLE#<role>`, the "People & roles" tab). The gateway reads the list
on every call and sends `effective_role_tools(role, stored)` to Cedar as `principal.roleTools`.

`ROLE_POLICIES` are Cedar `forbid`s, one per tool, that name no role ("deny unless the tool is in
`principal.roleTools`"), so the policies never change at runtime. A forbid always wins in Cedar, so a
role can only take tools away from the starter permits, never add one, and the starter forbids (bulk
export, campaign post) still bind every role, the Chief of Staff included. A missing or unknown role
gets an empty set, so every role forbid fires: fail closed. `LOCKED_TOOLS` stay with their roles
whatever an admin stores.
"""

from __future__ import annotations

from collections.abc import Iterable
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

# Tools only the listed roles may hold. An admin role editor can override ROLE_TOOLS but never grant these.
LOCKED_TOOLS: dict[str, frozenset[str]] = {"email_send_list": frozenset({"chief_of_staff"})}

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


def effective_role_tools(role: str | None, tools: Iterable[str] | None = None) -> frozenset[str]:
    """The tools a role may use: the override (or the ROLE_TOOLS default), governed tools only, minus
    locked tools the role may not hold. A missing or unknown role gets none, override or not (fail closed)."""
    if role not in ROLE_TOOLS:
        return frozenset()
    chosen = ROLE_TOOLS[role] if tools is None else tools
    return frozenset(
        tool
        for tool in chosen
        if tool in ROLE_GOVERNED_TOOLS and role in LOCKED_TOOLS.get(tool, {role})
    )


def role_allows(role: str | None, dotted: str, tools: Iterable[str] | None = None) -> bool:
    """True if this office role may use the registry tool, given an optional per-role tool override."""
    return mandate_tool_name(dotted) in effective_role_tools(role, tools)


def role_policy_id(tool: str) -> str:
    return f"role-{tool.replace('_', '-')}"


def _role_policy(tool: str) -> str:
    """Deny the tool unless it is in the role's list (`principal.roleTools`, from DynamoDB via the gateway).
    The policy names no role, so an admin's edit never edits a policy. A locked tool also names the only
    roles that may hold it, so Cedar keeps the lock even if a gateway sent a wrong list."""
    unless = f'principal has roleTools && principal.roleTools.contains("{tool}")'
    if tool in LOCKED_TOOLS:
        roles = ", ".join(f'"{r}"' for r in ROLE_TOOLS if r in LOCKED_TOOLS[tool])
        unless += f" && principal has role && [{roles}].contains(principal.role)"
    return f"""forbid(principal, action == Mandate::Action::"{tool}", resource)
  unless {{ {unless} }};"""


ROLE_POLICIES: dict[str, str] = {role_policy_id(t): _role_policy(t) for t in ROLE_GOVERNED_TOOLS}


def assignable_tools(role: str) -> tuple[str, ...]:
    """The tools an admin may give this role: governed tools (never an always-denied one), minus locked
    tools the role may not hold. An unknown role may hold nothing."""
    if role not in ROLE_TOOLS:
        return ()
    return tuple(t for t in ROLE_GOVERNED_TOOLS if role in LOCKED_TOOLS.get(t, {role}))


class RoleChangeError(ValueError):
    """A role edit an admin may not make. The message is plain words for the console."""


def validate_role_tools(role: str, tools: Iterable[str]) -> tuple[str, ...]:
    """Check an admin's new tool list for a role; return it in TOOLS order or raise RoleChangeError."""
    if role not in ROLE_TOOLS:
        raise RoleChangeError(f"There is no office role called {role!r}.")
    tools = list(tools)
    if len(set(tools)) != len(tools):
        raise RoleChangeError("A tool is listed twice.")
    for tool in tools:
        if tool not in TOOLS:
            raise RoleChangeError(f"There is no tool called {tool!r}.")
        if TOOLS[tool].always_denied:
            raise RoleChangeError(f"{tool} is blocked for everyone by policy and cannot be given to a role.")
        if tool not in assignable_tools(role):
            allowed = " and ".join(ROLE_LABELS[r] for r in ROLE_TOOLS if r in LOCKED_TOOLS[tool])
            raise RoleChangeError(f"{tool} is locked by policy to the {allowed} only.")
    return tuple(t for t in ROLE_GOVERNED_TOOLS if t in tools)


def describe_role_change(role: str, before: Iterable[str], after: Iterable[str]) -> list[str]:
    """Plain words for what an edit lets every staffer in this role (and their assistant) newly do or stop doing."""
    label = ROLE_LABELS.get(role, role)
    before, after = set(before), set(after)
    gained = [t for t in ROLE_GOVERNED_TOOLS if t in after - before]
    lost = [t for t in ROLE_GOVERNED_TOOLS if t in before - after]
    return [f"Every {label}'s assistant will be able to {tool_action(t)}." for t in gained] + [
        f"Every {label}'s assistant will no longer be able to {tool_action(t)}." for t in lost
    ]


def tool_action(tool: str) -> str:
    return TOOL_ACTIONS.get(tool, tool)


def role_label(role: str | None) -> str:
    return ROLE_LABELS.get(role or "", "No role")


def role_reason(role: str | None, tool: str) -> str:
    """Plain words for a role deny, e.g. "an Intern's assistant may not send email for the office"."""
    label = role_label(role)
    if label == "No role":
        return f"no office role is on file for this staffer, so the assistant may not {TOOL_ACTIONS.get(tool, tool)}"
    article = "an" if label[0] in "AEIOU" else "a"
    return f"{article} {label}'s assistant may not {TOOL_ACTIONS.get(tool, tool)}"
