"""Round 2: office-scoped recipientKnown, audit-feed hygiene for invalid tokens."""

from __future__ import annotations

import pytest
from identity_gateway.tokens import InvalidToken
from mandate_contracts import TOOLS
from mandate_contracts.ddb import CaseItem

DENIED = "denied by policy"


def _mail(to):
    return {"to": to, "subject": "Hi", "body": "Update"}


def test_constituent_emails_returns_only_the_offices_cases_lowercased(store):
    assert store.constituent_emails("rep-x") == {"jane.doe@example.com"}
    assert store.constituent_emails("rep-y") == {"bob@other.org"}


def test_constituent_emails_is_empty_for_an_office_without_cases(store):
    assert store.constituent_emails("rep-nobody") == set()


def test_a_new_rep_x_case_is_not_visible_to_rep_y(store):
    store.put_case(
        "case-3",
        CaseItem(office="rep-x", constituent="Kim", constituent_email="KIM@Example.com", topic="t", status="open"),
    )
    assert "kim@example.com" in store.constituent_emails("rep-x")
    assert "kim@example.com" not in store.constituent_emails("rep-y")


def test_email_to_another_offices_constituent_is_denied(gateway, make_token, handler_spy):
    out = gateway.guarded("email_send", _mail("bob@other.org"), make_token(office="rep-x"))
    assert out.event.decision == "DENY"
    assert out.event.reason == DENIED
    assert out.result is None
    assert handler_spy.calls == []


def test_email_to_own_offices_constituent_is_allowed(gateway, make_token, world):
    out = gateway.guarded("email_send", _mail(world.known_email), make_token())
    assert out.event.decision == "ALLOW"


def test_email_to_gov_address_is_still_allowed(gateway, make_token):
    out = gateway.guarded("email_send", _mail("someone@agency.gov"), make_token())
    assert out.event.decision == "ALLOW"


def test_invalid_token_event_records_unknown_for_a_junk_tool_name(gateway, store, events_of):
    with pytest.raises(InvalidToken):
        gateway.guarded("x" * 5000, {}, "not-a-token")
    events = events_of(store)
    assert len(events) == 1
    assert events[0].tool == "unknown"
    assert events[0].decision == "DENY"
    assert events[0].reason == "invalid token"


def test_invalid_token_event_keeps_a_real_tool_name(gateway, store, events_of):
    real = next(iter(TOOLS))
    with pytest.raises(InvalidToken):
        gateway.guarded(real, {}, "not-a-token")
    assert events_of(store)[0].tool == real
