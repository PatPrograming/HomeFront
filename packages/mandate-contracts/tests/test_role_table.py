"""The admin-editable role table (roles.py): what an admin may give a role, and what reaches Cedar.

Each test names the guard it proves; removing that guard makes it fail."""

from __future__ import annotations

import pytest

from mandate_contracts import TOOLS
from mandate_contracts.roles import (
    LOCKED_TOOLS,
    ROLE_GOVERNED_TOOLS,
    ROLE_TOOLS,
    RoleChangeError,
    assignable_tools,
    describe_role_change,
    effective_role_tools,
    validate_role_tools,
)

ALWAYS_DENIED = [n for n, spec in TOOLS.items() if spec.always_denied]


def test_there_is_an_always_denied_tool_to_test_with():
    assert ALWAYS_DENIED == ["records_bulk_export"]


@pytest.mark.parametrize("role", list(ROLE_TOOLS))
def test_an_always_denied_tool_can_never_be_given_to_a_role(role):
    with pytest.raises(RoleChangeError, match="blocked for everyone"):
        validate_role_tools(role, ["web_scrape", *ALWAYS_DENIED])
    assert not set(ALWAYS_DENIED) & set(assignable_tools(role))


@pytest.mark.parametrize("role", ["senator", "", "admin", "Chief_Of_Staff", "chief_of_staff "])
def test_a_role_that_does_not_exist_is_refused_and_holds_nothing(role):
    with pytest.raises(RoleChangeError, match="no office role"):
        validate_role_tools(role, ["web_scrape"])
    assert effective_role_tools(role, ["web_scrape"]) == frozenset()
    assert effective_role_tools(role, None) == frozenset()


@pytest.mark.parametrize("bad", [["Web_Scrape"], ["web_scrape "], ["web.scrape"], ["root"], ["web_scrape", "web_scrape"]])
def test_unknown_or_duplicate_tools_are_refused(bad):
    with pytest.raises(RoleChangeError):
        validate_role_tools("intern", bad)


def test_the_mailing_list_is_locked_to_the_chief_of_staff():
    assert LOCKED_TOOLS == {"email_send_list": frozenset({"chief_of_staff"})}


@pytest.mark.parametrize("role", list(ROLE_TOOLS))
def test_a_locked_tool_can_only_be_given_to_its_roles(role):
    """Guard: assignable_tools/validate_role_tools honour LOCKED_TOOLS (lead ruling: only the Chief of Staff
    may hold email_send_list, whatever an admin sends)."""
    if role == "chief_of_staff":
        assert "email_send_list" in validate_role_tools(role, ["email_send_list"])
        assert "email_send_list" in effective_role_tools(role, ["email_send_list"])
    else:
        assert "email_send_list" not in assignable_tools(role)
        with pytest.raises(RoleChangeError, match="locked by policy to the Chief of Staff only"):
            validate_role_tools(role, ["web_scrape", "email_send_list"])
        # Written straight to DynamoDB, it still never reaches Cedar.
        assert "email_send_list" not in effective_role_tools(role, ["email_send_list", "web_scrape"])


def test_a_stored_list_replaces_the_default_but_a_bad_item_cannot_add_a_blocked_tool():
    assert effective_role_tools("intern") == frozenset(ROLE_TOOLS["intern"])
    assert effective_role_tools("intern", []) == frozenset()  # an admin removed everything
    assert effective_role_tools("intern", ["case_read"]) == {"case_read"}
    assert effective_role_tools("intern", ["records_bulk_export", "nonsense", "web_scrape"]) == {"web_scrape"}


def test_valid_lists_come_back_in_tool_order():
    assert validate_role_tools("intern", ["web_scrape", "inbox_read"]) == ("inbox_read", "web_scrape")
    assert validate_role_tools("caseworker", []) == ()
    assert set(validate_role_tools("chief_of_staff", ROLE_GOVERNED_TOOLS)) == set(ROLE_GOVERNED_TOOLS)


def test_the_change_is_described_in_plain_words():
    lines = describe_role_change("intern", ["web_scrape"], ["web_scrape", "email_send"])
    assert lines == ["Every Intern's assistant will be able to send email for the office."]
    lines = describe_role_change("caseworker", ["case_read", "web_scrape"], ["web_scrape"])
    assert lines == ["Every Caseworker's assistant will no longer be able to open constituent case files."]
    assert describe_role_change("intern", ["web_scrape"], ["web_scrape"]) == []
