"""Verified Permissions (Cedar) contract (brief §6.5).

The gateway builds every IsAuthorized request with these helpers so the
request shape cannot drift. Two rules matter for security:

- `build_context` always sends every context key. If a key were missing,
  Cedar would skip any policy that reads it, and a skipped `forbid` fails open.
- Policies are keyed by a readable name. Decision events carry these names
  in `policyIds`; whoever creates the policies in AVP (WP7) keeps the map
  from AVP's generated policy id to this name.
"""

from __future__ import annotations

from typing import Any, Iterable

from .roles import ROLE_POLICIES, effective_role_tools

NAMESPACE = "Mandate"

CONTEXT_DEFAULTS: dict[str, bool | str] = {
    "delegatorActive": False,
    "recipientKnown": False,
    "account": "",
}

STARTER_POLICIES: dict[str, str] = {
    "case-read-same-office": """permit(principal, action == Mandate::Action::"case_read", resource)
  when { context.delegatorActive && resource.office == principal.office };""",
    "read-tools": """permit(principal, action in [Mandate::Action::"inbox_read", Mandate::Action::"datagov_search",
                             Mandate::Action::"web_scrape"], resource)
  when { context.delegatorActive };""",
    "email-send-known-recipient": """permit(principal, action == Mandate::Action::"email_send", resource)
  when { context.delegatorActive && context.recipientKnown };""",
    "social-post-official": """permit(principal, action == Mandate::Action::"social_post", resource)
  when { context.delegatorActive && context.account == "official" };""",
    "forbid-bulk-export": """forbid(principal, action == Mandate::Action::"records_bulk_export", resource);""",
    "forbid-campaign-post": """forbid(principal, action == Mandate::Action::"social_post", resource)
  when { principal.mode == "official" && context.account == "campaign" };""",
    # Added later as new files: setup.sh creates AVP policies but never updates one.
    "research-tools": """permit(principal, action in [Mandate::Action::"census_query", Mandate::Action::"openfda_search",
                             Mandate::Action::"eia_query", Mandate::Action::"nasa_search",
                             Mandate::Action::"noaa_climate", Mandate::Action::"nps_parks",
                             Mandate::Action::"bls_series", Mandate::Action::"fec_search",
                             Mandate::Action::"epa_search", Mandate::Action::"fcc_search",
                             Mandate::Action::"usaspending_search", Mandate::Action::"sam_entity_lookup"], resource)
  when { context.delegatorActive };""",
    "email-send-list-office": """permit(principal, action == Mandate::Action::"email_send_list", resource)
  when { context.delegatorActive };""",
    "social-reply-official": """permit(principal, action == Mandate::Action::"social_reply", resource)
  when { context.delegatorActive && context.account == "official" };""",
    "forbid-campaign-reply": """forbid(principal, action == Mandate::Action::"social_reply", resource)
  when { principal.mode == "official" && context.account == "campaign" };""",
    "report-save": """permit(principal, action == Mandate::Action::"report_save", resource)
  when { context.delegatorActive };""",
    # One forbid per tool for the staffer's office role (roles.py, docs/ROLES.md).
    **ROLE_POLICIES,
}


def _value(v: bool | str) -> dict[str, Any]:
    return {"boolean": v} if isinstance(v, bool) else {"string": v}


def build_context(**overrides: bool | str) -> dict[str, Any]:
    """AVP `context` with every key present; unknown keys are a bug, so they raise."""
    unknown = set(overrides) - set(CONTEXT_DEFAULTS)
    if unknown:
        raise KeyError(f"Unknown Cedar context keys: {sorted(unknown)}")
    merged = {**CONTEXT_DEFAULTS, **overrides}
    return {"contextMap": {k: _value(v) for k, v in merged.items()}}


def action(tool: str) -> dict[str, str]:
    return {"actionType": f"{NAMESPACE}::Action", "actionId": tool}


def agent_entity(
    agent_id: str, office: str, mode: str, role: str, role_tools: Iterable[str] | None = None
) -> dict[str, Any]:
    """`role` comes from the STAFFER item and `role_tools` from the role's DynamoDB list
    (`effective_role_tools`); None means the reviewed default for `role`. The role policies read only
    `roleTools`: an empty set (no or unknown role) fails every one of them."""
    tools = effective_role_tools(role, None) if role_tools is None else role_tools
    return {
        "identifier": {"entityType": f"{NAMESPACE}::Agent", "entityId": agent_id},
        "attributes": {
            "office": _value(office),
            "mode": _value(mode),
            "role": _value(role),
            "roleTools": {"set": [_value(t) for t in sorted(tools)]},
        },
    }


def case_entity(case_id: str, office: str) -> dict[str, Any]:
    """`office` must come from the CASE item in DynamoDB, never from tool input."""
    return {
        "identifier": {"entityType": f"{NAMESPACE}::Case", "entityId": case_id},
        "attributes": {"office": _value(office)},
    }


def tool_entity(tool: str) -> dict[str, Any]:
    return {"identifier": {"entityType": f"{NAMESPACE}::Tool", "entityId": tool}, "attributes": {}}
