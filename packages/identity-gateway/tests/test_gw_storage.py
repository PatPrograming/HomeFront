"""MandateStore over the single DynamoDB table (moto, no AWS)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from identity_gateway.storage import MandateStore, create_table
from mandate_contracts import DecisionEvent, GuardrailFlag
from mandate_contracts.ddb import (
    CaseItem,
    EmailItem,
    SessionItem,
    StafferItem,
    case_key,
    event_key,
    session_key,
    staffer_key,
)

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _event(n: int, ts: str | None = None, **over) -> DecisionEvent:
    fields = dict(
        id=f"evt-{n}",
        ts=ts or f"{TODAY}T10:00:0{n}.000Z",
        sid="sid-1",
        agent="agent-1",
        acting_for="maya",
        office="rep-x",
        tool="case_read",
        decision="ALLOW",
        reason="allowed",
    )
    fields.update(over)
    return DecisionEvent(**fields)


def _cursor(e: DecisionEvent) -> str:
    return event_key(e.ts, e.id)["sk"]


# ---- table ----------------------------------------------------------------


def test_create_table_has_string_pk_sk_and_on_demand_billing(dynamodb):
    table = create_table(dynamodb)
    desc = table.meta.client.describe_table(TableName="mandate")["Table"]
    keys = {k["AttributeName"]: k["KeyType"] for k in desc["KeySchema"]}
    types = {a["AttributeName"]: a["AttributeType"] for a in desc["AttributeDefinitions"]}
    assert keys == {"pk": "HASH", "sk": "RANGE"}
    assert types == {"pk": "S", "sk": "S"}
    assert desc["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"
    assert table.name == "mandate"


# ---- sessions and staffers ------------------------------------------------


def test_missing_session_and_staffer_read_as_none(store):
    assert store.get_session("nope") is None
    assert store.get_staffer("nobody") is None


def test_session_round_trips_and_uses_the_contract_key(store, table):
    item = SessionItem(
        status="active",
        agent="agent-9",
        acting_for="maya",
        office="rep-x",
        mode="official",
        created_at="2026-11-07T10:00:00Z",
    )
    store.put_session("sid-new", item)
    assert store.get_session("sid-new") == item
    raw = table.get_item(Key=session_key("sid-new"), ConsistentRead=True)["Item"]
    assert raw["actingFor"] == "maya"  # camelCase on the wire
    assert raw["status"] == "active"


def test_staffer_round_trips_and_can_be_deactivated(store, table):
    assert store.get_staffer("maya").active is True
    store.set_staffer_active("maya", False)
    assert store.get_staffer("maya").active is False
    assert store.get_staffer("maya").office == "rep-x"  # other fields kept
    assert table.get_item(Key=staffer_key("maya"), ConsistentRead=True)["Item"]["active"] is False
    store.set_staffer_active("maya", True)
    assert store.get_staffer("maya").active is True


def test_revoke_sessions_for_flips_only_that_staffers_active_sessions(store, world):
    extra = store.get_session(world.sid).model_copy(update={"status": "active"})
    store.put_session("sid-maya-2", extra)
    already = extra.model_copy(update={"status": "revoked"})
    store.put_session("sid-maya-old", already)

    count = store.revoke_sessions_for("maya")

    assert count == 2  # sid-maya-1 and sid-maya-2; the already revoked one is not counted
    assert store.get_session(world.sid).status == "revoked"
    assert store.get_session("sid-maya-2").status == "revoked"
    assert store.get_session("sid-maya-old").status == "revoked"
    assert store.get_session(world.chief_sid).status == "active"  # other staffer untouched


def test_revoke_sessions_for_is_idempotent(store):
    assert store.revoke_sessions_for("maya") == 1
    assert store.revoke_sessions_for("maya") == 0


def test_revoke_sessions_for_unknown_staffer_returns_zero(store):
    assert store.revoke_sessions_for("ghost") == 0


# ---- cases and emails -----------------------------------------------------


def test_case_round_trips(store, world, table):
    case = store.get_case(world.case_x)
    assert isinstance(case, CaseItem)
    assert case.office == "rep-x" and case.constituent == "Jane Doe"
    assert store.get_case("case-999") is None
    assert table.get_item(Key=case_key(world.case_x))["Item"]["constituentEmail"]


def test_email_round_trips_including_the_from_field(store, world):
    email = store.get_email(world.email_id)
    assert isinstance(email, EmailItem)
    assert email.from_ == "citizen@example.com"
    assert email.body == world.email_body
    assert store.get_email("email-999") is None


def test_constituent_emails_are_lower_cased_and_only_come_from_cases(store, world):
    assert store.constituent_emails("rep-x") == {"jane.doe@example.com"}
    assert store.constituent_emails("rep-y") == {"bob@other.org"}
    # the email item's sender and staffers must not leak into the known-recipient set
    assert "citizen@example.com" not in store.constituent_emails("rep-x")


# ---- events ---------------------------------------------------------------


def test_put_event_uses_the_contract_key(store, table):
    e = _event(1)
    store.put_event(e)
    raw = table.get_item(Key=event_key(e.ts, e.id), ConsistentRead=True)["Item"]
    assert raw["id"] == e.id and raw["actingFor"] == "maya"


def test_events_since_none_returns_todays_events_oldest_first(store):
    store.put_event(_event(3))
    store.put_event(_event(1))
    store.put_event(_event(2))
    events, cursor = store.events_since(None)
    assert [e.id for e in events] == ["evt-1", "evt-2", "evt-3"]
    assert cursor == _cursor(events[-1])


def test_events_since_none_ignores_other_days(store):
    store.put_event(_event(1, ts="2020-01-01T10:00:00.000Z"))
    store.put_event(_event(2))
    events, _ = store.events_since(None)
    assert [e.id for e in events] == ["evt-2"]


def test_events_since_cursor_returns_only_newer_events(store):
    e1, e2, e3 = _event(1), _event(2), _event(3)
    for e in (e1, e2, e3):
        store.put_event(e)
    events, cursor = store.events_since(_cursor(e1))
    assert [e.id for e in events] == ["evt-2", "evt-3"]
    assert cursor == _cursor(e3)


def test_events_since_with_nothing_new_echoes_the_cursor(store):
    e1 = _event(1)
    store.put_event(e1)
    events, cursor = store.events_since(_cursor(e1))
    assert events == []
    assert cursor == _cursor(e1)


def test_events_since_none_on_empty_log_has_no_cursor(store):
    events, cursor = store.events_since(None)
    assert events == [] and cursor is None


def test_events_since_limit_pages_through_the_log(store):
    made = [_event(n) for n in range(5)]
    for e in made:
        store.put_event(e)
    page1, c1 = store.events_since(None, limit=2)
    page2, c2 = store.events_since(c1, limit=2)
    page3, c3 = store.events_since(c2, limit=2)
    assert [e.id for e in page1] == ["evt-0", "evt-1"]
    assert [e.id for e in page2] == ["evt-2", "evt-3"]
    assert [e.id for e in page3] == ["evt-4"]
    assert c3 == _cursor(made[4])
    assert store.events_since(c3, limit=2) == ([], c3)


def test_event_fields_survive_a_round_trip(store):
    e = _event(
        1,
        decision="DENY",
        reason="denied by policy",
        policy_ids=["forbid-bulk-export"],
        atlas=["AML.T0053"],
        resource="case-2",
        guardrail=GuardrailFlag(flagged=True, categories=["PROMPT_ATTACK"]),
    )
    store.put_event(e)
    (got,), _ = store.events_since(None)
    assert got == e


# ---- ConsistentRead on every read ----------------------------------------


class SpyTable:
    """Thin proxy around a moto Table that records kwargs of every read call."""

    READS = ("get_item", "query", "scan")

    def __init__(self, table):
        self._table = table
        self.reads: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        attr = getattr(self._table, name)
        if name not in self.READS:
            return attr

        def spy(*args, **kwargs):
            self.reads.append((name, kwargs))
            return attr(*args, **kwargs)

        return spy


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.get_session("sid-maya-1"),
        lambda s: s.get_staffer("maya"),
        lambda s: s.get_case("case-1"),
        lambda s: s.get_email("email-1"),
        lambda s: s.constituent_emails("rep-x"),
        lambda s: s.events_since(None),
        lambda s: s.revoke_sessions_for("maya"),
    ],
    ids=[
        "get_session",
        "get_staffer",
        "get_case",
        "get_email",
        "constituent_emails",
        "events_since",
        "revoke_sessions_for",
    ],
)
def test_every_read_uses_consistent_read(store, table, call):
    store.put_event(_event(1))
    spy = SpyTable(table)
    call(MandateStore(spy))
    assert spy.reads, "the method never read from the table"
    for name, kwargs in spy.reads:
        assert kwargs.get("ConsistentRead") is True, f"{name} read without ConsistentRead=True"
