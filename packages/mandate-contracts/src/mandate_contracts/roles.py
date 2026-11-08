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

Role = Literal["chief_of_staff", "caseworker", "communications_director", "legislative_aide", "intern"]

ROLE_LABELS: dict[str, str] = {
    "chief_of_staff": "Chief of Staff",
    "caseworker": "Caseworker",
    "communications_director": "Communications Director",
    "legislative_aide": "Legislative Aide",
    "intern": "Intern",
}

# The tools each role may use. records_bulk_export is in no list: forbid-bulk-export blocks it for everyone.
ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "chief_of_staff": ("inbox_read", "case_read", "datagov_search", "web_scrape", "email_send", "social_post"),
    "caseworker": ("inbox_read", "case_read", "datagov_search", "web_scrape", "email_send"),
    "communications_director": ("inbox_read", "datagov_search", "web_scrape", "email_send", "social_post"),
    "legislative_aide": ("inbox_read", "datagov_search", "web_scrape"),
    "intern": ("datagov_search", "web_scrape"),
}

# The tools a role policy governs, in TOOLS order.
ROLE_GOVERNED_TOOLS = ("inbox_read", "case_read", "datagov_search", "web_scrape", "email_send", "social_post")

# What each tool does, for a staffer reading why their assistant was blocked.
_ACTIONS = {
    "inbox_read": "read the office inbox",
    "case_read": "open constituent case files",
    "datagov_search": "search public government data",
    "web_scrape": "read public web pages",
    "email_send": "send email for the office",
    "social_post": "post on the office's official account",
}


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
        return f"no office role is on file for this staffer, so the assistant may not {_ACTIONS.get(tool, tool)}"
    article = "an" if label[0] in "AEIOU" else "a"
    return f"{article} {label}'s assistant may not {_ACTIONS.get(tool, tool)}"
