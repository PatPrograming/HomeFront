"""The admin-editable role table (ROLE#<role> in DynamoDB) reaches the real Cedar engine on every call.

Real Cedar (cedarpy) on the committed policy files, moto DynamoDB. Offline: no AWS."""

from __future__ import annotations

import pytest
from test_gw_roles import CEDAR, cedar_gateway  # noqa: F401 (fixture)

from mandate_contracts import TOOLS
from mandate_contracts.ddb import RoleItem, StafferItem, role_key
from mandate_contracts.roles import ROLE_TOOLS

WHEN = "2026-11-08T10:00:00+00:00"


def _set_role_tools(store, role, tools):
    store.put_role_tools(role, RoleItem(tools=list(tools), updated_by="chiefofstaff", updated_at=WHEN))


def _as(store, office_role):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role=office_role))


def test_without_a_stored_list_each_role_gets_its_reviewed_default(store):
    for role, tools in ROLE_TOOLS.items():
        assert store.get_role_tools(role) == frozenset(tools)


def test_an_admin_edit_applies_mid_session_on_the_very_next_call(cedar_gateway, store, make_token, handler_spy):
    token = make_token()  # maya is a Caseworker; one token, one session for the whole test
    assert cedar_gateway.guarded("case_read", {"case_id": "case-1"}, token).allowed is True

    _set_role_tools(store, "caseworker", [t for t in ROLE_TOOLS["caseworker"] if t != "case_read"])
    out = cedar_gateway.guarded("case_read", {"case_id": "case-1"}, token)  # same token, still valid
    assert out.allowed is False and out.event.policy_ids == ["role-case-read"]

    _set_role_tools(store, "caseworker", ROLE_TOOLS["caseworker"])  # given back
    assert cedar_gateway.guarded("case_read", {"case_id": "case-1"}, token).allowed is True


def test_an_added_tool_is_allowed_on_the_next_call_but_the_starter_permits_still_apply(
    cedar_gateway, store, make_token
):
    _as(store, "intern")
    token = make_token()
    assert cedar_gateway.guarded("inbox_read", {"email_id": "email-1"}, token).allowed is False
    _set_role_tools(store, "intern", ["inbox_read", "case_read", "datagov_search", "web_scrape"])
    assert cedar_gateway.guarded("inbox_read", {"email_id": "email-1"}, token).allowed is True
    # A role list only lifts the role forbid; another office's case is still refused by the starter permit.
    assert cedar_gateway.guarded("case_read", {"case_id": "case-2"}, token).allowed is False


def test_an_empty_list_takes_every_tool_away(cedar_gateway, store, make_token):
    _set_role_tools(store, "caseworker", [])
    token = make_token()
    for tool, args in [("datagov_search", {"query": "x"}), ("inbox_read", {"email_id": "email-1"})]:
        assert cedar_gateway.guarded(tool, args, token).allowed is False


@pytest.mark.parametrize("bad", ["records_bulk_export", "senator_tool"])
def test_a_bad_item_written_straight_to_dynamodb_cannot_unlock_a_blocked_tool(store, table, bad):
    # Bypasses the API validator entirely.
    table.put_item(Item={**role_key("intern"), "tools": [bad, "web_scrape"], "updatedBy": "x", "updatedAt": WHEN})
    assert store.get_role_tools("intern") == {"web_scrape"}


def test_bulk_export_stays_denied_even_if_the_stored_list_names_it(cedar_gateway, store, table, make_token):
    _as(store, "chief_of_staff")
    table.put_item(
        Item={**role_key("chief_of_staff"), "tools": list(TOOLS), "updatedBy": "x", "updatedAt": WHEN}
    )
    out = cedar_gateway.guarded("records_bulk_export", {"query": "all"}, make_token())
    assert out.allowed is False and "forbid-bulk-export" in out.event.policy_ids


def test_a_role_item_for_another_role_does_not_leak(cedar_gateway, store, make_token):
    _set_role_tools(store, "chief_of_staff", ROLE_TOOLS["chief_of_staff"])
    _set_role_tools(store, "intern", ["case_read"])
    token = make_token()  # maya is a Caseworker: neither item applies to her
    assert store.get_role_tools("caseworker") == frozenset(ROLE_TOOLS["caseworker"])
    assert cedar_gateway.guarded("social_post", {"account": "official", "text": "hi"}, token).allowed is False


def test_a_draft_approved_after_a_role_edit_is_checked_against_the_new_list(cedar_gateway, store, make_token, world):
    held = cedar_gateway.guarded("email_send", {"to": world.known_email, "subject": "s", "body": "b"}, make_token())
    assert held.result["status"] == "held_for_review"
    _set_role_tools(store, "caseworker", ["inbox_read", "case_read"])
    out = cedar_gateway.send_draft(held.result["draftId"], store.get_draft(held.result["draftId"]), {})
    assert out.allowed is False and out.event.policy_ids == ["role-email-send"]
