"""Earned autonomy: outbound calls are held for review until the staffer lets the agent send that kind alone."""

from __future__ import annotations

import pytest

from mandate_contracts.ddb import AutonomyItem, EmailItem

REPLY = {"subject": "Re: your case", "body": "We called the post office today."}


def _held_draft(store, out):
    return store.get_draft(out.result["draftId"])


def test_a_send_maya_has_not_allowed_is_held_not_run(gateway, store, make_token, world, handler_spy):
    out = gateway.guarded("email_send", {"to": world.known_email, **REPLY}, make_token())

    assert out.allowed and out.event.reason == "held for staff review"
    assert out.result["status"] == "held_for_review"
    assert handler_spy.calls == [], "a held email must not be sent"
    draft = _held_draft(store, out)
    assert (draft.status, draft.tool, draft.category, draft.acting_for) == ("pending", "email_send", "new_conversation", "maya")
    assert draft.args == {"to": world.known_email, **REPLY}


def test_a_reply_in_an_allowed_category_is_sent(trusted_replies, gateway, make_token, world, handler_spy):
    args = {"to": world.known_email, "in_reply_to": world.trusted_email_id, **REPLY}
    out = gateway.guarded("email_send", args, make_token())

    assert out.allowed and out.event.reason == "allowed"
    assert handler_spy.calls


def test_category_comes_from_the_inbox_not_the_agent(trusted_replies, gateway, make_token, world, handler_spy):
    """Pointing in_reply_to at a trusted email does not help when writing to someone else."""
    args = {"to": "staffer@house.gov", "in_reply_to": world.trusted_email_id, **REPLY}
    out = gateway.guarded("email_send", args, make_token())

    assert out.result["status"] == "held_for_review" and out.result["category"] == "new_conversation"
    assert handler_spy.calls == []


def test_a_locked_category_is_held_even_if_its_level_says_auto(gateway, store, make_token, world, handler_spy):
    store.put_email("email-pii", EmailItem(from_=world.known_email, subject="SSN", body="...", kind="sensitive"))
    store.put_autonomy("maya", "sensitive", AutonomyItem(level="auto"))
    args = {"to": world.known_email, "in_reply_to": "email-pii", **REPLY}
    out = gateway.guarded("email_send", args, make_token())

    assert out.result["status"] == "held_for_review"
    assert handler_spy.calls == []


def test_cedar_still_decides_first_so_a_denied_send_leaves_no_draft(gateway, store, make_token, handler_spy):
    out = gateway.guarded("email_send", {"to": "archive@evil.example", **REPLY}, make_token())

    assert not out.allowed and out.result is None
    assert store.pending_drafts_for("maya") == []


def test_official_posts_are_held_until_allowed(gateway, make_token, handler_spy):
    out = gateway.guarded("social_post", {"account": "official", "text": "Town hall Tuesday"}, make_token())
    assert out.result["status"] == "held_for_review" and out.result["category"] == "social_post"
    assert handler_spy.calls == []


# ---- the staffer approves a draft ---------------------------------------------


def _hold_one(gateway, store, make_token, world):
    out = gateway.guarded("email_send", {"to": world.known_email, **REPLY}, make_token())
    draft_id = out.result["draftId"]
    return draft_id, store.get_draft(draft_id)


def test_an_approved_draft_is_sent_once(gateway, store, make_token, world, handler_spy):
    from identity_gateway.gateway import DraftAlreadyDecided

    draft_id, draft = _hold_one(gateway, store, make_token, world)
    out = gateway.send_draft(draft_id, draft, {"body": "New wording"})

    assert out.allowed and out.event.reason == "approved by staff"
    assert len(handler_spy.calls) == 1
    sent = store.get_draft(draft_id)
    assert sent.status == "sent" and sent.edited is True
    with pytest.raises(DraftAlreadyDecided):
        gateway.send_draft(draft_id, draft, {})
    assert len(handler_spy.calls) == 1


def test_an_offboarded_staffer_cannot_approve(gateway, store, make_token, world, handler_spy):
    draft_id, draft = _hold_one(gateway, store, make_token, world)
    store.set_staffer_active("maya", False)

    out = gateway.send_draft(draft_id, draft, {})

    assert not out.allowed and out.event.reason == "session revoked / delegator inactive"
    assert handler_spy.calls == []
    assert store.get_draft(draft_id).status == "pending"


def test_a_failed_send_goes_back_to_the_queue(gateway, store, make_token, world, handler_spy):
    draft_id, draft = _hold_one(gateway, store, make_token, world)
    handler_spy.boom = True

    out = gateway.send_draft(draft_id, draft, {})

    assert not out.allowed
    assert store.get_draft(draft_id).status == "pending"


# ---- when to offer more autonomy ---------------------------------------------


@pytest.mark.parametrize(
    ("category", "item", "expected"),
    [
        ("case_status", AutonomyItem(approved_unchanged=3), True),
        ("case_status", AutonomyItem(approved_unchanged=2), False),
        ("case_status", AutonomyItem(approved_unchanged=3, rejected=1), False),  # 75% unchanged
        ("case_status", AutonomyItem(approved_unchanged=3, snoozed_at=3), False),  # "not now" just said
        ("case_status", AutonomyItem(approved_unchanged=6, snoozed_at=3), True),  # 3 more reviews since
        ("case_status", AutonomyItem(level="auto", approved_unchanged=9), False),
        ("social_post", AutonomyItem(approved_unchanged=4), False),  # posts need 5
        ("sensitive", AutonomyItem(approved_unchanged=50), False),  # locked
    ],
)
def test_should_suggest_auto(category, item, expected):
    from identity_gateway.autonomy import should_suggest_auto

    assert should_suggest_auto(category, item) is expected
