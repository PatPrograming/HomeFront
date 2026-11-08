"""An approved plan limits its session to the plan's tools: everything else is denied before Cedar."""

from __future__ import annotations

NOT_IN_PLAN = "not in the approved plan"
INBOX = {"email_id": "email-1"}


def test_before_any_plan_inbox_read_behaves_as_before(gateway, store, make_token, authorizer, world, events_of):
    assert store.plan_tools(world.sid) is None
    out = gateway.guarded("inbox_read", INBOX, make_token())
    assert out.allowed is True and out.event.reason == "allowed"
    assert [r.tool for r in authorizer.requests] == ["inbox_read"]


def test_after_approval_a_tool_outside_the_plan_is_denied_without_asking_cedar(
    gateway, store, make_token, authorizer, world, events_of
):
    store.set_plan_tools(world.sid, ["web_scrape", "social_post"])
    out = gateway.guarded("inbox_read", INBOX, make_token())
    assert out.allowed is False
    assert (out.event.decision, out.event.reason, out.event.tool) == ("DENY", NOT_IN_PLAN, "inbox_read")
    assert authorizer.requests == []  # Cedar never asked
    assert [e.reason for e in events_of(store)] == [NOT_IN_PLAN]


def test_a_plan_tool_still_takes_the_normal_path(gateway, store, make_token, authorizer, world, maya_posts):
    store.set_plan_tools(world.sid, ["web_scrape", "social_post"])
    out = gateway.guarded("social_post", {"account": "official", "text": "Town hall Tuesday"}, make_token())
    assert [r.tool for r in authorizer.requests] == ["social_post"]  # Cedar decided it
    assert out.event.reason != NOT_IN_PLAN
    assert out.event.reason == "held for staff review"  # review hold unchanged


def test_a_new_approved_plan_replaces_the_set(gateway, store, make_token, world):
    store.set_plan_tools(world.sid, ["web_scrape"])
    assert gateway.guarded("inbox_read", INBOX, make_token()).event.reason == NOT_IN_PLAN
    store.set_plan_tools(world.sid, ["inbox_read"])
    assert gateway.guarded("inbox_read", INBOX, make_token()).event.reason == "allowed"
