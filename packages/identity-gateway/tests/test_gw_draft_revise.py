"""C13: the agent's `draft_revise` tool edits ONE pending draft in place ("make it shorter").

Spec: docs/loops/agent-permissions.md "Design addendum: research -> one draft -> edit until satisfied".
Revising sends nothing and never creates a draft; approval (send_draft) re-checks everything.
Each test names, in its docstring, what removing its guard would let through. Every test runs on both
policy engines (the in-Python double and real Cedar).
"""

from __future__ import annotations

import types

import pytest
from mandate_contracts.ddb import SessionItem, StafferItem

from conftest import now_iso

DENIED = "denied by policy"
REVOKED = "session revoked / delegator inactive"
NEEDS = "needs connection"
MAIL = {"to": "a@b.gov", "subject": "Hello", "body": "A long first draft of the reply."}
POST = {"account": "official", "text": "Town hall Tuesday at 6pm", "platform": "bluesky"}
PLATFORMS = ("x", "bluesky", "linkedin", "facebook")


@pytest.fixture(autouse=True)
def _no_connection_module(monkeypatch):
    import identity_gateway.gateway as gw

    monkeypatch.setattr(gw, "connections", None, raising=False)


@pytest.fixture
def gw_(engine_gateway):
    return engine_gateway


def _hold(gw_, make_token, tool, args, **token):
    out = gw_.guarded(tool, args, make_token(**token))
    assert out.event.reason == "held for staff review", out.event
    return out.result["draftId"]


def _revise(gw_, make_token, draft_id, **fields):
    token = fields.pop("token", {})
    return gw_.guarded("draft_revise", {"draft_id": draft_id, **fields}, make_token(**token))


def _prime(gw_, store, make_token, draft_id):
    """One legitimate revision by the owner first, so a DENY below can only come from the guard under test
    (not from the tool being unknown). Returns the draft as the deny must leave it."""
    assert _revise(gw_, make_token, draft_id, body="primed").allowed
    return store.get_draft(draft_id)


def _unchanged(store, draft_id, before):
    assert store.get_draft(draft_id) == before


# ---- the happy path: same draft, version + 1 ---------------------------------------------------


def test_revising_an_email_draft_updates_the_same_draft_and_never_creates_another(
    gw_, store, events_of, make_token, handler_spy
):
    """Without updating in place, every "make it shorter" would pile up a second draft for the staffer to sort out."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    before = store.get_draft(draft_id)
    out = _revise(gw_, make_token, draft_id, subject="Short", body="Shorter.", to="other@c.gov")
    assert out.allowed and out.result is not None
    pending = store.pending_drafts_for("maya")
    assert [i for i, _ in pending] == [draft_id]  # same id, count unchanged
    after = store.get_draft(draft_id)
    assert after.version == 2 and before.version == 1
    assert (after.args["subject"], after.args["body"], after.args["to"]) == ("Short", "Shorter.", "other@c.gov")
    assert after.status == "pending" and after.edited is False  # an agent revision is not a staffer edit
    for kept in ("sid", "agent", "acting_for", "office", "tool", "category", "created_at"):
        assert getattr(after, kept) == getattr(before, kept), kept
    assert handler_spy.calls == []  # revising sends nothing


def test_a_successful_revision_clears_the_revising_flag(gw_, store, make_token):
    """Without clearing `revising`, the staffer's card would show "assistant is revising..." forever after
    the agent has already answered."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    assert store.get_draft(draft_id).revising is False  # default
    store.put_draft(draft_id, store.get_draft(draft_id).model_copy(update={"revising": True}))
    assert _revise(gw_, make_token, draft_id, body="Shorter.").allowed
    after = store.get_draft(draft_id)
    assert after.revising is False and after.version == 2


def test_each_revision_adds_one_to_the_version(gw_, store, make_token):
    """Without the increment, the staffer's card could not tell a stale view from the latest wording."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    _revise(gw_, make_token, draft_id, body="Two.")
    _revise(gw_, make_token, draft_id, body="Three.")
    assert store.get_draft(draft_id).version == 3
    assert store.get_draft(draft_id).args["body"] == "Three."


def test_a_revision_changes_only_the_fields_it_names(gw_, store, make_token):
    """Without merging, a body-only revision would blank the subject and recipient."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    _revise(gw_, make_token, draft_id, body="Only the body.")
    args = store.get_draft(draft_id).args
    assert args == {**MAIL, "body": "Only the body."}


def test_a_revision_writes_exactly_one_allow_event_for_draft_revise(gw_, store, events_of, make_token):
    """Without the audit event, edits to what leaves the office would be invisible in the feed."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    _revise(gw_, make_token, draft_id, body="Shorter.")
    revise_events = [e for e in events_of(store) if e.tool == "draft_revise"]
    assert len(revise_events) == 1
    event = revise_events[0]
    assert (event.decision, event.sid, event.acting_for) == ("ALLOW", "sid-maya-1", "maya")


def test_a_social_draft_text_and_platform_can_be_revised(gw_, store, make_token, maya_posts):
    """Without social fields in the editable set, a post could never be re-worded or moved to another platform."""
    draft_id = _hold(gw_, make_token, "social_post", POST)
    out = _revise(gw_, make_token, draft_id, text="Join us Tuesday.", platform="linkedin")
    assert out.allowed
    after = store.get_draft(draft_id)
    assert (after.args["text"], after.args["platform"], after.version) == ("Join us Tuesday.", "linkedin", 2)
    assert len(store.pending_drafts_for("maya")) == 1


def test_a_social_reply_draft_text_can_be_revised_and_its_target_cannot(gw_, store, make_token, maya_posts):
    """Without pinning reply_to, a revision could aim the held reply at a different post."""
    args = {"account": "official", "reply_to": "https://x.com/a/status/1", "text": "Thanks", "platform": "bluesky"}
    draft_id = _hold(gw_, make_token, "social_reply", args)
    _revise(gw_, make_token, draft_id, text="Thank you!", reply_to="https://x.com/z/status/9")
    after = store.get_draft(draft_id)
    assert after.args["text"] == "Thank you!" and after.args["reply_to"] == "https://x.com/a/status/1"


# ---- what a revision can never change ----------------------------------------------------------


def test_account_in_reply_to_and_tool_cannot_be_changed(gw_, store, make_token, maya_posts, handler_spy):
    """Without dropping these keys, a held official post could be moved to the campaign account, a reply
    could be re-pointed at another email, or the draft turned into a mailing-list send."""
    post_id = _hold(gw_, make_token, "social_post", POST)
    mail_id = _hold(gw_, make_token, "email_send", {**MAIL, "in_reply_to": "email-1"})
    _revise(gw_, make_token, post_id, text="New", account="campaign", tool="email_send_list")
    _revise(gw_, make_token, mail_id, body="New", in_reply_to="email-9", tool="email_send_list", account="campaign")
    post, mail = store.get_draft(post_id), store.get_draft(mail_id)
    assert (post.tool, post.args["account"], post.args["text"]) == ("social_post", "official", "New")
    assert (mail.tool, mail.args.get("in_reply_to"), "account" in mail.args) == ("email_send", "email-1", False)
    assert handler_spy.calls == []


def test_a_mailing_list_draft_gets_no_recipient_from_a_revision(gw_, store, make_token, world, handler_spy):
    """Without limiting email_send_list to subject and body, the agent could swap the whole-list send for one
    to an address of its choosing; the list must come from DynamoDB at send time only."""
    token = {"act": "chiefofstaff", "sid": world.chief_sid}
    draft_id = _hold(gw_, make_token, "email_send_list", {"subject": "News", "body": "Hello"}, **token)
    _revise(gw_, make_token, draft_id, body="Hello all", to="evil@evil.test", token=token)
    after = store.get_draft(draft_id)
    assert after.args["body"] == "Hello all" and "to" not in after.args and after.tool == "email_send_list"


def test_an_email_draft_gets_no_social_fields_from_a_revision(gw_, store, make_token):
    """Without per-tool editable fields, an email draft would grow text/platform keys it never had."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    _revise(gw_, make_token, draft_id, body="Shorter.", text="stray", platform="x")
    assert set(store.get_draft(draft_id).args) == set(MAIL)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_four_supported_platforms_are_accepted(gw_, store, make_token, maya_posts, platform):
    """Without accepting all four, the staffer could not move a post to the platform they want."""
    draft_id = _hold(gw_, make_token, "social_post", POST)
    assert _revise(gw_, make_token, draft_id, platform=platform).allowed
    assert store.get_draft(draft_id).args["platform"] == platform


@pytest.mark.parametrize("platform", ["myspace", "X", "", "x ", "twitter"])
def test_an_unknown_platform_is_denied_and_the_draft_is_untouched(gw_, store, make_token, maya_posts, platform):
    """Without the platform allow-list, a draft could be pointed at any string and fail only at send time."""
    draft_id = _hold(gw_, make_token, "social_post", POST)
    before = _prime(gw_, store, make_token, draft_id)
    out = _revise(gw_, make_token, draft_id, text="changed too", platform=platform)
    assert out.allowed is False and out.event.decision == "DENY" and out.event.reason == DENIED
    _unchanged(store, draft_id, before)


# ---- only the draft's own session may revise it ------------------------------------------------


def test_the_same_staffer_in_another_session_cannot_revise_the_draft(gw_, store, make_token, handler_spy):
    """Without the draft.sid == claims.sid check, any other chat session of the staffer (or a stolen older token)
    could rewrite a draft the staffer is reviewing."""
    store.put_session(
        "sid-maya-2",
        SessionItem(status="active", agent="agent-1", acting_for="maya", office="rep-x", mode="official", created_at=now_iso()),
    )
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    before = _prime(gw_, store, make_token, draft_id)
    out = _revise(gw_, make_token, draft_id, body="hijacked", token={"sid": "sid-maya-2"})
    assert out.allowed is False and out.event.reason == DENIED and out.result is None
    _unchanged(store, draft_id, before)


def test_another_staffer_cannot_revise_the_draft(gw_, store, make_token, world):
    """Without the ownership check, the chief's assistant could rewrite a caseworker's held email."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    before = _prime(gw_, store, make_token, draft_id)
    out = _revise(gw_, make_token, draft_id, body="hijacked", token={"act": "chiefofstaff", "sid": world.chief_sid})
    assert out.allowed is False and out.event.reason == DENIED
    _unchanged(store, draft_id, before)


# ---- only a pending draft can be revised -------------------------------------------------------


@pytest.mark.parametrize("status", ["sent", "rejected"])
def test_a_decided_draft_cannot_be_revised(gw_, store, make_token, status):
    """Without the pending check, a draft already sent or rejected could be rewritten after the fact,
    leaving the record of what went out wrong."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    _prime(gw_, store, make_token, draft_id)
    decided = store.get_draft(draft_id).model_copy(update={"status": status, "decided_at": now_iso()})
    assert store.decide_draft(draft_id, decided)
    out = _revise(gw_, make_token, draft_id, body="too late")
    assert out.allowed is False and out.event.reason == DENIED
    _unchanged(store, draft_id, decided)


def test_an_unknown_draft_id_is_denied_and_creates_no_draft(gw_, store, make_token):
    """Without the existence check, a revision of a made-up id could create a draft out of thin air."""
    out = _revise(gw_, make_token, "no-such-draft", body="x", to="a@b.gov", subject="s")
    assert out.allowed is False and out.event.reason == DENIED
    assert store.get_draft("no-such-draft") is None and store.pending_drafts_for("maya") == []


# ---- the session and staffer must still be active ----------------------------------------------


def test_a_revoked_session_cannot_revise(gw_, store, make_token, world):
    """Without the fresh session read, a revoked session could keep editing what the staffer will approve."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    before = _prime(gw_, store, make_token, draft_id)
    token = make_token()
    store.revoke_sessions_for("maya")
    out = gw_.guarded("draft_revise", {"draft_id": draft_id, "body": "x"}, token)
    assert out.allowed is False and out.event.reason == REVOKED
    _unchanged(store, draft_id, before)


def test_an_offboarded_staffer_cannot_revise(gw_, store, make_token):
    """Without the staffer.active check, an offboarded staffer's assistant could still change queued drafts."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    before = _prime(gw_, store, make_token, draft_id)
    token = make_token()
    store.set_staffer_active("maya", False)
    out = gw_.guarded("draft_revise", {"draft_id": draft_id, "body": "x"}, token)
    assert out.allowed is False and out.event.reason == REVOKED
    _unchanged(store, draft_id, before)


# ---- a revision is a draft, not a send: approval still decides ---------------------------------


def test_a_revision_to_an_unknown_recipient_is_accepted_but_approval_is_denied_by_cedar(
    gw_, store, events_of, make_token, handler_spy
):
    """Without re-running Cedar at approval, revising `to` to a stranger would be a way around the
    known-recipient rule: the agent edits the held draft and the staffer's click sends it."""
    draft_id = _hold(gw_, make_token, "email_send", MAIL)
    out = _revise(gw_, make_token, draft_id, to="stranger@example.com")
    assert out.allowed and store.get_draft(draft_id).args["to"] == "stranger@example.com"
    approved = gw_.send_draft(draft_id, store.get_draft(draft_id), {})
    assert approved.allowed is False and approved.event.reason == DENIED
    assert handler_spy.calls == []
    assert store.get_draft(draft_id).status == "pending"


def test_revising_a_post_to_x_without_an_x_connection_needs_connection_at_approval(
    gw_, store, make_token, maya_posts, monkeypatch, handler_spy
):
    """Without the connection check at approval, moving a draft to X would try to post through an account
    the staffer never connected (or, worse, fall back to a shared one)."""
    import identity_gateway.gateway as gw

    class NeedsConnection(Exception):
        def __init__(self, provider):
            super().__init__(provider)
            self.provider = provider

    d = types.SimpleNamespace(NeedsConnection=NeedsConnection, missing=set(), x=[], gmail=[])
    d.provider_for_tool = lambda tool, args: "x" if tool in ("social_post", "social_reply") and (args.get("platform") or "x") == "x" else None
    d.needs_connection_result = lambda exc: {
        "status": "needs_connection", "provider": exc.provider, "label": "X",
        "connectUrl": f"/account?connect={exc.provider}", "note": "NOT sent.",
    }

    def require_connection(staffer_id, provider):
        if provider in d.missing:
            raise NeedsConnection(provider)

    d.require_connection = require_connection
    d.post_x = lambda staffer_id, text, reply_to=None: d.x.append(text) or {"status": "posted", "provider": "x"}
    d.send_gmail = lambda *a, **k: d.gmail.append(a) or {"status": "sent"}
    monkeypatch.setattr(gw, "connections", d, raising=False)

    draft_id = _hold(gw_, make_token, "social_post", POST)  # bluesky: no connection needed to hold it
    assert _revise(gw_, make_token, draft_id, platform="x").allowed
    d.missing = {"x"}
    out = gw_.send_draft(draft_id, store.get_draft(draft_id), {})
    assert out.event.reason == NEEDS and out.result["provider"] == "x"
    assert d.x == [] and handler_spy.calls == []
    assert store.get_draft(draft_id).status == "pending"
