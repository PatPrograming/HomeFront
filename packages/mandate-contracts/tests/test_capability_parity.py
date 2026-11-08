"""C1: every Agents-tab capability has a mandate tool, Cedar action, role policy and starter permit,
and one role table (roles.py ROLE_TOOLS) drives both the assistant path and the Agents-tab path.

Expected values come from docs/loops/agent-permissions.md (Design 1 to 3). The tool list is derived from
the mcp_tools registry, so a new registry tool with no mandate twin fails here.
"""

import json
import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
ROOT = PKG.parents[1]
sys.path.insert(0, str(PKG / "src"))
sys.path.insert(0, str(ROOT / "packages" / "mcp-tools" / "src"))

from mandate_contracts import TOOLS  # noqa: E402
from mandate_contracts.cedar import STARTER_POLICIES  # noqa: E402
from mandate_contracts.roles import (  # noqa: E402
    ROLE_GOVERNED_TOOLS,
    ROLE_POLICIES,
    ROLE_TOOLS,
    role_policy_id,
)
from mcp_tools.registry import TOOL_REGISTRY  # noqa: E402

RESEARCH = (
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
NEW_TOOLS = ("email_send_list", "social_reply", "usaspending_search", "sam_entity_lookup", "report_save", *RESEARCH)
STARTER_ADDED = ("research-tools", "email-send-list-office", "social-reply-official", "forbid-campaign-reply", "report-save")

CURRENT = {
    "caseworker": ("inbox_read", "case_read", "datagov_search", "web_scrape", "email_send"),
    "communications_director": ("inbox_read", "datagov_search", "web_scrape", "email_send", "social_post"),
    "legislative_aide": ("inbox_read", "datagov_search", "web_scrape"),
}


def _dotted_to_mandate(dotted: str) -> str:
    return dotted.replace(".", "_")


# ---- registry <-> TOOLS ----------------------------------------------------------------------


def test_every_registry_tool_has_a_mandate_twin_in_TOOLS():
    missing = [d for d in TOOL_REGISTRY if _dotted_to_mandate(d) not in TOOLS]
    assert missing == []


def test_mandate_tool_name_maps_every_registry_tool():
    from mandate_contracts.roles import mandate_tool_name

    for dotted in TOOL_REGISTRY:
        assert mandate_tool_name(dotted) == _dotted_to_mandate(dotted)


@pytest.mark.parametrize(
    "bad",
    ["Email.send", " email.send", "email.send ", "email_send", "ｅmail.send", "email․send", "email.send​", "", "nope.tool"],
)
def test_mandate_tool_name_rejects_anything_but_the_exact_registry_name(bad):
    from mandate_contracts.roles import mandate_tool_name

    with pytest.raises(KeyError):
        mandate_tool_name(bad)


def test_the_15_new_tools_are_in_TOOLS_and_governed():
    for name in NEW_TOOLS:
        assert name in TOOLS, name
        assert name in ROLE_GOVERNED_TOOLS, name
        assert not TOOLS[name].always_denied, name


def test_governed_tools_are_every_tool_except_the_always_denied_ones():
    assert set(ROLE_GOVERNED_TOOLS) == {n for n, s in TOOLS.items() if not s.always_denied}
    assert list(ROLE_GOVERNED_TOOLS) == [n for n in TOOLS if n in ROLE_GOVERNED_TOOLS]


# ---- input models ----------------------------------------------------------------------------


def _fields(name: str) -> set[str]:
    model = TOOLS[name].input_model
    return {(f.alias or n) for n, f in model.model_fields.items()}


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_new_tool_input_covers_every_registry_property(name):
    dotted = next(d for d in TOOL_REGISTRY if _dotted_to_mandate(d) == name)
    registry_props = set(TOOL_REGISTRY[dotted].input_schema["properties"])
    assert registry_props <= _fields(name)


def test_email_send_list_input_has_only_subject_and_body_and_drops_recipients():
    assert _fields("email_send_list") == {"subject", "body"}
    parsed = TOOLS["email_send_list"].input_model.model_validate(
        {"subject": "s", "body": "b", "recipients": ["x@evil.test"], "to": "x@evil.test"}
    )
    assert parsed.model_dump() == {"subject": "s", "body": "b"}


def test_social_reply_input_shape_and_default_platform():
    model = TOOLS["social_reply"].input_model
    assert _fields("social_reply") == {"account", "reply_to", "text", "platform"}
    parsed = model.model_validate({"account": "official", "reply_to": "123", "text": "hi"})
    assert parsed.platform == "x"


def test_social_post_gains_platform_defaulting_to_x():
    parsed = TOOLS["social_post"].input_model.model_validate({"account": "official", "text": "hi"})
    assert parsed.platform == "x"


def test_census_query_accepts_for_and_in_aliases():
    parsed = TOOLS["census_query"].input_model.model_validate(
        {"year": 2022, "dataset": "acs/acs5", "get": "NAME", "for": "state:06", "in": "county:001"}
    )
    dumped = parsed.model_dump(by_alias=True)
    assert dumped["for"] == "state:06" and dumped["in"] == "county:001"


def test_usaspending_search_takes_an_integer_fiscal_year():
    model = TOOLS["usaspending_search"].input_model
    assert model.model_validate({"keywords": "roads", "fiscal_year": "2024"}).fiscal_year == 2024


def test_untrusted_output_is_exactly_inbox_scrape_research_and_procurement():
    expected = {"inbox_read", "web_scrape", "usaspending_search", "sam_entity_lookup", *RESEARCH}
    assert {n for n, s in TOOLS.items() if s.untrusted_output} == expected


def test_tools_json_schema_file_lists_every_tool_in_order():
    committed = json.loads((PKG / "schemas" / "tools.json").read_text())
    assert [t["name"] for t in committed] == list(TOOLS)


# ---- Cedar -----------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", ROLE_GOVERNED_TOOLS)
def test_every_governed_tool_has_a_role_forbid_in_the_starter_policies(tool):
    pid = role_policy_id(tool)
    assert pid in ROLE_POLICIES
    assert pid in STARTER_POLICIES
    text = STARTER_POLICIES[pid]
    assert text.startswith("forbid(") and f'Mandate::Action::"{tool}"' in text


@pytest.mark.parametrize("tool", [t for t in ROLE_GOVERNED_TOOLS])
def test_every_governed_tool_has_a_starter_permit_naming_its_action(tool):
    permits = [
        name
        for name, text in STARTER_POLICIES.items()
        if not name.startswith("role-") and text.startswith("permit(") and f'Mandate::Action::"{tool}"' in text
    ]
    assert permits, f"no starter permit for {tool}"


def test_role_policy_ids_are_hyphenated_tool_names():
    assert role_policy_id("email_send_list") == "role-email-send-list"
    assert "role-email-send-list" in ROLE_POLICIES
    assert "role-social-reply" in ROLE_POLICIES
    assert "role-report-save" in ROLE_POLICIES


def test_five_new_starter_policies_exist():
    for name in STARTER_ADDED:
        assert name in STARTER_POLICIES, name


def test_research_tools_permit_covers_the_apis_and_procurement_when_delegator_active():
    text = STARTER_POLICIES["research-tools"]
    assert text.startswith("permit(")
    for tool in (*RESEARCH, "usaspending_search", "sam_entity_lookup"):
        assert f'Mandate::Action::"{tool}"' in text, tool
    assert "context.delegatorActive" in text


def test_email_send_list_and_report_save_permits_need_only_an_active_delegator():
    for name, tool in (("email-send-list-office", "email_send_list"), ("report-save", "report_save")):
        text = STARTER_POLICIES[name]
        assert text.startswith("permit(") and f'Mandate::Action::"{tool}"' in text
        assert "context.delegatorActive" in text


def test_social_reply_permit_requires_the_official_account():
    text = STARTER_POLICIES["social-reply-official"]
    assert text.startswith("permit(") and 'Mandate::Action::"social_reply"' in text
    assert "context.delegatorActive" in text and 'context.account == "official"' in text


def test_campaign_reply_is_forbidden_in_official_mode():
    text = STARTER_POLICIES["forbid-campaign-reply"]
    assert text.startswith("forbid(") and 'Mandate::Action::"social_reply"' in text
    assert 'principal.mode == "official"' in text and 'context.account == "campaign"' in text


def test_the_existing_starter_policies_are_not_edited():
    # setup.sh never updates an existing AVP policy, so additions must be new files.
    assert 'Mandate::Action::"social_reply"' not in STARTER_POLICIES["read-tools"]
    assert 'Mandate::Action::"social_reply"' not in STARTER_POLICIES["forbid-campaign-post"]
    assert "census_query" not in STARTER_POLICIES["read-tools"]


def test_committed_cedar_files_match_STARTER_POLICIES_exactly():
    committed = {p.stem: p.read_text() for p in (PKG / "policies").glob("*.cedar")}
    assert set(committed) == set(STARTER_POLICIES)
    assert committed == {k: v + "\n" for k, v in STARTER_POLICIES.items()}


@pytest.mark.parametrize("name", [*STARTER_ADDED, *(role_policy_id(t) for t in NEW_TOOLS)])
def test_each_new_cedar_file_exists(name):
    assert (PKG / "policies" / f"{name}.cedar").is_file()


def test_there_is_one_role_policy_per_governed_tool_and_the_starter_total_is_6_plus_5_plus_22_plus_1():
    governed = [n for n, t in TOOLS.items() if not t.always_denied]
    assert len([n for n in STARTER_POLICIES if n.startswith("role-")]) == len(governed) == 22
    assert len(STARTER_POLICIES) == 6 + 5 + len(governed) + 1  # + draft-revise


# ---- one role table --------------------------------------------------------------------------


def test_role_values_are_subsets_of_governed_tools():
    for role, tools in ROLE_TOOLS.items():
        assert set(tools) <= set(ROLE_GOVERNED_TOOLS), role
        assert len(set(tools)) == len(tools), role


def test_chief_of_staff_has_every_governed_tool():
    assert set(ROLE_TOOLS["chief_of_staff"]) == set(ROLE_GOVERNED_TOOLS)
    assert len(ROLE_TOOLS["chief_of_staff"]) == len([n for n, t in TOOLS.items() if not t.always_denied])


def test_email_send_list_is_chief_of_staff_only():
    assert [r for r, tools in ROLE_TOOLS.items() if "email_send_list" in tools] == ["chief_of_staff"]
    assert '"chief_of_staff"' in ROLE_POLICIES["role-email-send-list"]
    for other in ("caseworker", "communications_director", "legislative_aide", "intern"):
        assert f'"{other}"' not in ROLE_POLICIES["role-email-send-list"]


def test_caseworker_is_current_plus_research_and_report_save():
    assert set(ROLE_TOOLS["caseworker"]) == {*CURRENT["caseworker"], *RESEARCH, "report_save", "draft_revise"}


def test_communications_director_adds_social_reply_research_and_report_save():
    expected = {*CURRENT["communications_director"], "social_reply", *RESEARCH, "report_save", "draft_revise"}
    assert set(ROLE_TOOLS["communications_director"]) == expected


def test_legislative_aide_adds_research_procurement_and_report_save():
    expected = {*CURRENT["legislative_aide"], *RESEARCH, "usaspending_search", "sam_entity_lookup", "report_save", "draft_revise"}
    assert set(ROLE_TOOLS["legislative_aide"]) == expected


def test_intern_gets_exactly_the_seven_listed_tools():
    assert set(ROLE_TOOLS["intern"]) == {
        "datagov_search",
        "web_scrape",
        "census_query",
        "nasa_search",
        "nps_parks",
        "noaa_climate",
        "draft_revise",
    }


def test_only_communications_director_and_chief_can_reply_on_social():
    allowed = {r for r, tools in ROLE_TOOLS.items() if "social_reply" in tools}
    assert allowed == {"chief_of_staff", "communications_director"}


def test_only_chief_and_legislative_aide_get_procurement_tools():
    for tool in ("usaspending_search", "sam_entity_lookup"):
        assert {r for r, t in ROLE_TOOLS.items() if tool in t} == {"chief_of_staff", "legislative_aide"}


def test_role_policy_lists_are_generated_from_ROLE_TOOLS():
    for tool in ROLE_GOVERNED_TOOLS:
        listed = set(re.findall(r'"(\w+)"', re.search(r"\[(.*?)\]", ROLE_POLICIES[role_policy_id(tool)]).group(1)))
        assert listed == {r for r, tools in ROLE_TOOLS.items() if tool in tools}, tool


def test_role_allows_reads_the_same_table_for_dotted_registry_names():
    from mandate_contracts.roles import role_allows

    for role in ROLE_TOOLS:
        for dotted in TOOL_REGISTRY:
            assert role_allows(role, dotted) is (_dotted_to_mandate(dotted) in ROLE_TOOLS[role]), (role, dotted)


def test_role_allows_fails_closed_for_unknown_or_missing_roles():
    from mandate_contracts.roles import role_allows

    for role in ("admin", "Chief_Of_Staff", "", "staffer", None):
        assert role_allows(role, "datagov.search") is False, role


def test_role_allows_blocks_a_caseworker_from_the_mailing_list():
    from mandate_contracts.roles import role_allows

    assert role_allows("chief_of_staff", "email.send_list") is True
    assert role_allows("caseworker", "email.send_list") is False


# ---- plain words -----------------------------------------------------------------------------


def test_TOOL_ACTIONS_has_plain_words_for_every_governed_tool():
    from mandate_contracts.roles import TOOL_ACTIONS

    assert set(ROLE_GOVERNED_TOOLS) <= set(TOOL_ACTIONS)
    for tool in ROLE_GOVERNED_TOOLS:
        assert TOOL_ACTIONS[tool].strip() and "_" not in TOOL_ACTIONS[tool], tool


def test_role_reason_for_a_new_tool_uses_plain_words_not_the_tool_name():
    from mandate_contracts.roles import TOOL_ACTIONS, role_reason

    reason = role_reason("caseworker", "email_send_list")
    assert TOOL_ACTIONS["email_send_list"] in reason
    assert "email_send_list" not in reason
    assert reason.startswith("a Caseworker's assistant may not")


# ---- TypeScript mirror -----------------------------------------------------------------------


def test_ts_TOOL_NAMES_and_ROLE_TOOLS_equal_python():
    ts = (PKG / "ts" / "index.ts").read_text()
    names = re.search(r"TOOL_NAMES = \[(.*?)\]", ts, re.S).group(1)
    assert re.findall(r'"(\w+)"', names) == list(TOOLS)
    body = re.search(r"ROLE_TOOLS: .*? = \{(.*?)\};", ts, re.S).group(1)
    parsed = {m[0]: tuple(re.findall(r'"(\w+)"', m[1])) for m in re.findall(r"(\w+): \[(.*?)\]", body)}
    assert parsed == ROLE_TOOLS


def test_ts_Reason_includes_needs_connection():
    from typing import get_args

    from mandate_contracts import Reason

    assert "needs connection" in get_args(Reason)
    ts = (PKG / "ts" / "index.ts").read_text()
    assert '"needs connection"' in re.search(r"type Reason =(.*?);", ts, re.S).group(1)


def test_draft_item_accepts_the_new_outbound_tools():
    from typing import get_args

    from mandate_contracts.ddb import DraftItem

    literal = get_args(DraftItem.model_fields["tool"].annotation)
    assert {"email_send_list", "social_reply"} <= set(literal)


# ---- locked tools ----------------------------------------------------------------------------
# Lead's ruling: email_send_list is locked to chief_of_staff. The admin role editor can never grant it
# to another role, and it is always held for review. One contracts-level LOCKED_TOOLS map feeds both
# the Cedar role forbids and the role editor.


def test_LOCKED_TOOLS_is_exactly_email_send_list_for_chief_of_staff():
    from mandate_contracts.roles import LOCKED_TOOLS

    assert LOCKED_TOOLS == {"email_send_list": frozenset({"chief_of_staff"})}


def test_every_locked_tool_is_a_governed_tool():
    from mandate_contracts.roles import LOCKED_TOOLS

    assert set(LOCKED_TOOLS) <= set(ROLE_GOVERNED_TOOLS)


def test_every_locked_tool_appears_in_ROLE_TOOLS_only_for_its_allowed_roles():
    from mandate_contracts.roles import LOCKED_TOOLS

    for tool, allowed in LOCKED_TOOLS.items():
        holders = {r for r, tools in ROLE_TOOLS.items() if tool in tools}
        assert holders == set(allowed), tool


def test_generated_role_forbid_for_each_locked_tool_names_only_its_allowed_roles():
    from mandate_contracts.roles import LOCKED_TOOLS

    for tool, allowed in LOCKED_TOOLS.items():
        text = ROLE_POLICIES[role_policy_id(tool)]
        listed = set(re.findall(r'"(\w+)"', re.search(r"\[(.*?)\]", text).group(1)))
        assert listed == set(allowed), tool


def test_role_allows_locked_tool_is_false_for_a_disallowed_role_even_with_an_override_granting_it():
    from mandate_contracts.roles import role_allows

    override = {"email_send_list", "datagov_search"}
    assert role_allows("intern", "email.send_list", tools=override) is False


def test_role_allows_override_replaces_the_default_tool_set():
    from mandate_contracts.roles import role_allows

    override = {"email_send_list", "datagov_search"}
    assert role_allows("intern", "datagov.search", tools=override) is True
    assert role_allows("intern", "web.scrape", tools=override) is False  # in intern's default, not in override


def test_role_allows_chief_of_staff_loses_a_tool_missing_from_the_override():
    from mandate_contracts.roles import role_allows

    assert role_allows("chief_of_staff", "email.send_list", tools={"datagov_search"}) is False


def test_role_allows_with_tools_none_uses_the_ROLE_TOOLS_default():
    from mandate_contracts.roles import role_allows

    assert role_allows("intern", "web.scrape", tools=None) is True
    assert role_allows("chief_of_staff", "email.send_list", tools=None) is True


def test_effective_role_tools_defaults_to_ROLE_TOOLS_when_there_is_no_override():
    from mandate_contracts.roles import effective_role_tools

    for role, tools in ROLE_TOOLS.items():
        assert effective_role_tools(role, None) == frozenset(tools), role


def test_effective_role_tools_override_replaces_default_and_is_a_frozenset():
    from mandate_contracts.roles import effective_role_tools

    result = effective_role_tools("intern", {"datagov_search", "census_query"})
    assert isinstance(result, frozenset)
    assert result == frozenset({"datagov_search", "census_query"})


def test_effective_role_tools_drops_ungoverned_names_from_an_override():
    from mandate_contracts.roles import effective_role_tools

    result = effective_role_tools("caseworker", {"datagov_search", "records_bulk_export", "not_a_tool"})
    assert result == frozenset({"datagov_search"})


def test_effective_role_tools_removes_locked_tools_for_disallowed_roles():
    from mandate_contracts.roles import effective_role_tools

    override = {"email_send_list", "datagov_search"}
    assert effective_role_tools("intern", override) == frozenset({"datagov_search"})
    assert effective_role_tools("caseworker", override) == frozenset({"datagov_search"})


def test_effective_role_tools_keeps_a_locked_tool_for_its_allowed_role():
    from mandate_contracts.roles import effective_role_tools

    assert effective_role_tools("chief_of_staff", {"email_send_list", "datagov_search"}) == frozenset(
        {"email_send_list", "datagov_search"}
    )


def test_effective_role_tools_for_an_unknown_role_is_empty_even_with_an_override():
    from mandate_contracts.roles import effective_role_tools

    for role in ("admin", "", None):
        assert effective_role_tools(role, None) == frozenset(), role
        assert effective_role_tools(role, {"datagov_search"}) == frozenset(), role


def test_autonomy_locks_the_mailing_list_category():
    gateway_src = ROOT / "packages" / "identity-gateway" / "src"
    sys.path.insert(0, str(gateway_src))
    try:
        from identity_gateway import autonomy
    except ImportError:
        pytest.skip("identity_gateway.autonomy is not importable from the contracts test environment")
    assert "mailing_list" in autonomy.LOCKED


# ---- draft_revise (research -> one draft -> edit until satisfied) ------------------------------
# Spec: docs/loops/agent-permissions.md "Design addendum: research -> one draft -> edit until satisfied".

OPTIONAL_REVISE_FIELDS = {"subject", "body", "to", "text", "platform"}
# Mandate tools with no registry twin: gateway-only reads, the always-denied export, and draft_revise
# (it has no Agents-tab counterpart, so it is deliberately not in the mcp_tools registry).
MANDATE_ONLY = {"inbox_read", "case_read", "records_bulk_export", "draft_revise"}


def test_draft_revise_is_the_one_documented_new_mandate_only_tool():
    """Registry parity is not weakened: the tools without a registry twin are exactly the three
    pre-existing gateway-only ones plus draft_revise. Any other mandate-only tool fails here."""
    registry = {_dotted_to_mandate(d) for d in TOOL_REGISTRY}
    assert {n for n in TOOLS if n not in registry} == MANDATE_ONLY


def test_draft_revise_is_in_TOOLS():
    assert "draft_revise" in TOOLS


def test_draft_revise_input_requires_only_draft_id():
    model = TOOLS["draft_revise"].input_model
    assert {n for n, f in model.model_fields.items() if f.is_required()} == {"draft_id"}
    assert _fields("draft_revise") == {"draft_id", *OPTIONAL_REVISE_FIELDS}


def test_draft_revise_input_rejects_a_missing_draft_id():
    with pytest.raises(Exception):
        TOOLS["draft_revise"].input_model.model_validate({"body": "shorter"})


def test_draft_revise_input_drops_extra_keys_so_account_and_tool_cannot_be_changed():
    parsed = TOOLS["draft_revise"].input_model.model_validate(
        {"draft_id": "d1", "body": "b", "account": "campaign", "in_reply_to": "email-1", "tool": "email_send_list", "office": "rep-y"}
    )
    dumped = parsed.model_dump(exclude_none=True)
    assert dumped == {"draft_id": "d1", "body": "b"}


def test_draft_revise_is_not_untrusted_and_not_always_denied():
    assert TOOLS["draft_revise"].untrusted_output is False
    assert TOOLS["draft_revise"].always_denied is False
    assert "draft_revise" in ROLE_GOVERNED_TOOLS


def test_draft_revise_has_a_cedar_action_and_a_committed_starter_permit_needing_an_active_delegator():
    assert role_policy_id("draft_revise") == "role-draft-revise"
    text = STARTER_POLICIES["draft-revise"]
    assert text.startswith("permit(") and 'Mandate::Action::"draft_revise"' in text
    assert "context.delegatorActive" in text
    assert (PKG / "policies" / "draft-revise.cedar").read_text() == text + "\n"


@pytest.mark.parametrize("role", list(ROLE_TOOLS))
def test_every_role_may_hold_draft_revise(role):
    """Revising sends nothing (approval re-checks everything), so no role is denied it."""
    assert "draft_revise" in ROLE_TOOLS[role]


def test_draft_revise_has_plain_words_for_a_role_deny():
    from mandate_contracts.roles import TOOL_ACTIONS

    assert TOOL_ACTIONS["draft_revise"].strip() and "_" not in TOOL_ACTIONS["draft_revise"]


def test_draft_item_version_defaults_to_1_and_round_trips():
    from mandate_contracts.ddb import DraftItem

    base = dict(
        status="pending", sid="s", agent="a", acting_for="maya", office="rep-x", tool="email_send",
        category="new_conversation", args={"to": "a@b.gov"}, created_at="t",
    )
    item = DraftItem(**base)
    assert item.version == 1
    assert item.revising is False  # set by the staffer's revise request, cleared by the agent's draft_revise
    assert DraftItem(**base, revising=True).revising is True
    assert DraftItem.model_validate(item.model_dump(mode="json", by_alias=True)).version == 1
    assert DraftItem(**base, version=3).version == 3


def test_Reason_includes_edited_by_staff_in_python_and_typescript():
    from typing import get_args

    from mandate_contracts import Reason

    assert "edited by staff" in get_args(Reason)
    ts = (PKG / "ts" / "index.ts").read_text()
    assert '"edited by staff"' in re.search(r"type Reason =(.*?);", ts, re.S).group(1)
