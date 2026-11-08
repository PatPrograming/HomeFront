"""Lane I connection gate: outbound tools need the staffer's Gmail or X account connected.

`identity_gateway.connections` is Lane I's module and is not merged yet, so these tests inject a
double with the agreed signatures through the gateway's module-level `connections` name (which is
None when the module is absent). The gate sits after Cedar ALLOW and before the autonomy hold.
"""

from __future__ import annotations

import types

import pytest
from mandate_contracts.ddb import AutonomyItem, StafferItem

NEEDS = "needs connection"
HELD = "held for staff review"
APPROVED = "approved by staff"
SEND_ARGS = {"to": "a@b.gov", "subject": "Hi", "body": "Hello"}
POST_ARGS = {"account": "official", "text": "Town hall Tuesday"}
REPLY_ARGS = {"account": "official", "reply_to": "https://x.com/a/status/1", "text": "Thanks", "platform": "x"}


class NeedsConnection(Exception):
    def __init__(self, provider):
        super().__init__(provider)
        self.provider = provider


class ProviderError(Exception):
    pass


def _provider_for_tool(tool, args):
    if tool in ("email_send", "email_send_list"):
        return "gmail"
    if tool in ("social_post", "social_reply") and (args.get("platform") or "x") == "x":
        return "x"
    return None


def _needs_connection_result(exc):
    return {
        "status": "needs_connection",
        "provider": exc.provider,
        "label": {"gmail": "Gmail", "x": "X"}[exc.provider],
        "connectUrl": f"/account?connect={exc.provider}",
        "note": "NOT sent. Ask the staffer to connect the account first.",
    }


@pytest.fixture
def conn(monkeypatch):
    """The Lane I double, installed on the gateway. `missing` is the set of unconnected providers."""
    import identity_gateway.gateway as gw

    d = types.ModuleType("identity_gateway.connections")
    d.NeedsConnection, d.ProviderError, d.CONNECTABLE = NeedsConnection, ProviderError, ("gmail", "x")
    d.missing = set()
    d.required = []  # (staffer_id, provider)
    d.gmail = []  # (staffer_id, to, subject, body, in_reply_to)
    d.x = []  # (staffer_id, text, reply_to)
    d.send_error = None
    d.provider_calls = []

    def require_connection(staffer_id, provider):
        d.required.append((staffer_id, provider))
        if provider in d.missing:
            raise NeedsConnection(provider)

    def send_gmail(staffer_id, to, subject, body, in_reply_to=None):
        if d.send_error:
            raise d.send_error
        d.gmail.append((staffer_id, to, subject, body, in_reply_to))
        return {"status": "sent", "provider": "gmail"}

    def post_x(staffer_id, text, reply_to=None):
        if d.send_error:
            raise d.send_error
        d.x.append((staffer_id, text, reply_to))
        return {"status": "posted", "provider": "x"}

    def provider_for_tool(tool, args):
        d.provider_calls.append(tool)
        return _provider_for_tool(tool, args)

    d.require_connection, d.send_gmail, d.post_x = require_connection, send_gmail, post_x
    d.needs_connection_result, d.provider_for_tool = _needs_connection_result, provider_for_tool
    monkeypatch.setattr(gw, "connections", d, raising=False)
    return d


@pytest.fixture
def poster(store):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="communications_director"))


@pytest.fixture
def gw_(engine_gateway):
    return engine_gateway


# ---- the gate: after Cedar, before the hold ---------------------------------------------------


@pytest.mark.parametrize(
    "tool,args,provider",
    [("email_send", SEND_ARGS, "gmail"), ("social_post", POST_ARGS, "x"), ("social_reply", REPLY_ARGS, "x")],
)
def test_a_missing_connection_denies_with_the_needs_connection_result_and_keeps_no_draft(
    gw_, store, events_of, make_token, poster, conn, handler_spy, tool, args, provider
):
    """Without the gate, the call would be held (or sent) as if the account were connected, and the
    staffer would find out only at approval time."""
    conn.missing = {provider}
    out = gw_.guarded(tool, args, make_token())
    event = events_of(store)[-1]
    assert out.allowed is False and event.decision == "DENY" and event.reason == NEEDS
    assert out.result == _needs_connection_result(NeedsConnection(provider))
    assert out.result["status"] == "needs_connection" and out.result["connectUrl"] == f"/account?connect={provider}"
    assert store.pending_drafts_for("maya") == [] and handler_spy.calls == []
    assert event.atlas == []  # not an attack: no ATLAS deny labels
    assert conn.required == [("maya", provider)]  # asked about the acting staffer


def test_a_missing_connection_denies_the_list_email_for_the_chief(gw_, store, make_token, world, conn):
    """The mailing-list email goes out through the staffer's Gmail, so it is gated too."""
    conn.missing = {"gmail"}
    out = gw_.guarded("email_send_list", {"subject": "s", "body": "b"}, make_token(act="chiefofstaff", sid=world.chief_sid))
    assert out.event.reason == NEEDS and out.result["provider"] == "gmail"
    assert store.pending_drafts_for("chiefofstaff") == []


def test_a_cedar_deny_comes_first_so_a_forbidden_call_never_asks_about_connections(gw_, store, make_token, conn):
    """Without ordering the gate after Cedar, an intern would learn to connect Gmail for a tool it may not use."""
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    conn.missing = {"gmail"}
    out = gw_.guarded("email_send", SEND_ARGS, make_token())
    assert out.event.reason == "denied by policy" and out.event.policy_ids == ["role-email-send"]
    assert conn.required == []


def test_with_a_connection_the_call_is_held_as_before_and_nothing_is_sent(gw_, store, make_token, conn, handler_spy):
    """The gate must not send: the hold still decides whether the agent may act alone."""
    out = gw_.guarded("email_send", SEND_ARGS, make_token())
    assert out.event.reason == HELD and out.result["status"] == "held_for_review"
    assert conn.required == [("maya", "gmail")] and conn.gmail == [] and handler_spy.calls == []


def test_a_call_with_the_connection_missing_is_denied_even_when_the_category_is_auto(gw_, store, make_token, conn, trusted_replies):
    """Without the gate before the autonomy check, 'auto' would try to send with no account."""
    conn.missing = {"gmail"}
    args = {**SEND_ARGS, "to": trusted_replies.known_email, "in_reply_to": trusted_replies.trusted_email_id}
    out = gw_.guarded("email_send", args, make_token())
    assert out.event.reason == NEEDS and conn.gmail == []


# ---- providers with no gate -------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["bluesky", "linkedin", "facebook"])
def test_platforms_without_a_connection_keep_the_share_link_handler_and_no_gate(
    gw_, store, make_token, poster, conn, handler_spy, platform
):
    """Bluesky, LinkedIn and Facebook stay on share links: gating them would block a working path."""
    store.put_autonomy("maya", "social_post", AutonomyItem(level="auto"))
    conn.missing = {"gmail", "x"}
    out = gw_.guarded("social_post", {**POST_ARGS, "platform": platform}, make_token())
    assert out.allowed and out.event.reason == "allowed"
    assert conn.required == [] and conn.x == [] and handler_spy.calls == ["social.post"]


def test_a_social_post_with_no_platform_is_an_x_post_and_is_gated(gw_, make_token, poster, conn):
    """A missing platform defaults to X, so it needs the X connection."""
    conn.missing = {"x"}
    out = gw_.guarded("social_post", POST_ARGS, make_token())
    assert out.event.reason == NEEDS and out.result["provider"] == "x"


# ---- sending through the connected account ----------------------------------------------------


def _approve(gw_, store, held, text=None):
    draft_id = held.result["draftId"]
    return draft_id, gw_.send_draft(draft_id, store.get_draft(draft_id), text or {})


def test_approving_an_email_sends_through_gmail_not_the_ses_handler(gw_, store, make_token, conn, handler_spy):
    held = gw_.guarded("email_send", {**SEND_ARGS, "in_reply_to": "email-1"}, make_token())
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert conn.gmail == [("maya", "a@b.gov", "Hi", "Hello", "email-1")] and "email.send" not in handler_spy.calls


def test_approving_a_list_email_sends_one_gmail_per_dynamodb_recipient(gw_, store, make_token, world, conn, handler_spy):
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    held = gw_.guarded("email_send_list", {"subject": "News", "body": "Hello", "recipients": ["evil@evil.com"]}, token)
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert [(c[0], c[1], c[2], c[3]) for c in conn.gmail] == [("chiefofstaff", "jane.doe@example.com", "News", "Hello")]
    assert "email.send_list" not in handler_spy.calls


def test_a_social_post_and_reply_go_out_through_x_when_allowed_alone(gw_, store, make_token, poster, conn, handler_spy):
    store.put_autonomy("maya", "social_post", AutonomyItem(level="auto"))
    store.put_autonomy("maya", "social_reply", AutonomyItem(level="auto"))
    post = gw_.guarded("social_post", POST_ARGS, make_token())
    reply = gw_.guarded("social_reply", REPLY_ARGS, make_token())
    assert post.event.reason == "allowed" and reply.event.reason == "allowed"
    assert conn.x == [("maya", "Town hall Tuesday", None), ("maya", "Thanks", "https://x.com/a/status/1")]
    assert handler_spy.calls == []


# ---- send_draft rechecks, errors deny ---------------------------------------------------------


def test_send_draft_rechecks_the_connection_and_returns_the_draft_to_pending(gw_, store, make_token, conn, handler_spy):
    """Without the recheck at approval, a staffer who disconnected Gmail after the hold would see 'sent'."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    conn.missing = {"gmail"}
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed is False and out.event.reason == NEEDS and out.result["provider"] == "gmail"
    assert conn.gmail == [] and handler_spy.calls == []
    assert store.get_draft(draft_id).status == "pending"


def test_a_sender_that_raises_needs_connection_gets_the_needs_connection_deny(gw_, store, make_token, conn):
    """A token that expired between the check and the send is a connection problem, not a crash."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())

    def expired(*a, **k):
        raise NeedsConnection("gmail")

    conn.send_gmail = expired
    draft_id, out = _approve(gw_, store, held)
    assert out.event.reason == NEEDS and out.result["status"] == "needs_connection"
    assert store.get_draft(draft_id).status == "pending"


def test_a_provider_error_on_approval_is_a_deny_and_the_draft_stays_pending(gw_, store, make_token, conn):
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    conn.send_error = ProviderError("gmail said 500")
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed is False and out.event.reason == "denied by policy" and out.result is None
    assert store.get_draft(draft_id).status == "pending"


def test_a_provider_error_on_a_direct_send_is_a_deny(gw_, store, make_token, poster, conn):
    store.put_autonomy("maya", "social_post", AutonomyItem(level="auto"))
    conn.send_error = ProviderError("x said 429")
    out = gw_.guarded("social_post", POST_ARGS, make_token())
    assert out.allowed is False and out.event.reason == "denied by policy" and out.result is None


# ---- module absent: exactly as today ----------------------------------------------------------


def test_with_the_module_absent_outbound_calls_behave_as_today(gw_, store, make_token, monkeypatch, handler_spy):
    """The gateway must run without Lane I: held, then the SES handler on approval."""
    import identity_gateway.gateway as gw

    monkeypatch.setattr(gw, "connections", None, raising=False)
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    assert held.event.reason == HELD
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED and handler_spy.calls == ["email.send"]


# ---- the chat tells the staffer: a server-side message with a Connect button -----------------------


def _chat(store, sid):
    return store.chat_since(sid, None)[0]


def _said(name, label):
    return f"I couldn't send this: {name} hasn't connected {label}. Nothing was sent."


@pytest.mark.parametrize(
    "tool,args,provider,label",
    [
        ("email_send", SEND_ARGS, "gmail", "Gmail"),
        ("social_post", POST_ARGS, "x", "X"),
        ("social_reply", REPLY_ARGS, "x", "X"),
    ],
)
def test_a_missing_connection_posts_one_assistant_chat_message_with_a_connect_button(
    gw_, store, events_of, make_token, world, poster, conn, tool, args, provider, label
):
    """Without the server-side post, the staffer sees nothing unless the agent happens to repeat the deny."""
    conn.missing = {provider}
    out = gw_.guarded(tool, args, make_token())
    messages = _chat(store, world.sid)
    assert len(messages) == 1
    assert (messages[0].role, messages[0].text, messages[0].connect) == ("assistant", _said("maya", label), provider)
    # the audit event and the no-draft rule are unchanged
    assert out.event.decision == "DENY" and events_of(store)[-1].reason == NEEDS
    assert store.pending_drafts_for("maya") == []


def test_the_connect_message_goes_to_the_calling_session_only(gw_, store, make_token, world, conn):
    conn.missing = {"gmail"}
    gw_.guarded("email_send", SEND_ARGS, make_token())
    assert len(_chat(store, world.sid)) == 1 and _chat(store, world.chief_sid) == []


def test_the_list_email_for_the_chief_posts_the_message_with_the_chiefs_name(gw_, store, make_token, world, conn):
    conn.missing = {"gmail"}
    gw_.guarded("email_send_list", {"subject": "s", "body": "b"}, make_token(act="chiefofstaff", sid=world.chief_sid))
    messages = _chat(store, world.chief_sid)
    assert [(m.text, m.connect) for m in messages] == [(_said("chiefofstaff", "Gmail"), "gmail")]


def test_a_connected_send_posts_no_connect_message(gw_, store, make_token, world, poster, conn):
    store.put_autonomy("maya", "social_post", AutonomyItem(level="auto"))
    gw_.guarded("email_send", SEND_ARGS, make_token())  # held
    gw_.guarded("social_post", POST_ARGS, make_token())  # sent through X
    assert _chat(store, world.sid) == []


def test_a_cedar_deny_posts_no_connect_message(gw_, store, make_token, world, conn):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    conn.missing = {"gmail"}
    gw_.guarded("email_send", SEND_ARGS, make_token())
    assert _chat(store, world.sid) == []


def test_send_draft_with_a_missing_connection_posts_to_the_drafts_session_and_keeps_it_pending(
    gw_, store, make_token, world, conn
):
    """The approve click is the other place the send can fail; the chat must say so there too."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    assert _chat(store, world.sid) == []
    conn.missing = {"gmail"}
    draft_id, out = _approve(gw_, store, held)
    messages = _chat(store, world.sid)
    assert [(m.role, m.text, m.connect) for m in messages] == [("assistant", _said("maya", "Gmail"), "gmail")]
    assert out.event.reason == NEEDS and store.get_draft(draft_id).status == "pending"


def test_send_draft_when_the_sender_raises_needs_connection_also_posts(gw_, store, make_token, world, conn):
    held = gw_.guarded("email_send", SEND_ARGS, make_token())

    def expired(*a, **k):
        raise NeedsConnection("gmail")

    conn.send_gmail = expired
    _approve(gw_, store, held)
    assert [(m.text, m.connect) for m in _chat(store, world.sid)] == [(_said("maya", "Gmail"), "gmail")]


def test_a_connected_approval_posts_no_connect_message(gw_, store, make_token, world, conn):
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    _approve(gw_, store, held)
    assert _chat(store, world.sid) == []


def test_chat_message_item_round_trips_the_connect_field():
    from mandate_contracts.ddb import ChatMessageItem

    plain = ChatMessageItem(id="1", ts="t", role="assistant", text="hi")
    assert plain.connect is None
    assert plain.model_dump(mode="json")["connect"] is None
    for provider in ("gmail", "x"):
        item = ChatMessageItem(id="1", ts="t", role="assistant", text="hi", connect=provider)
        dumped = item.model_dump(mode="json")
        assert dumped["connect"] == provider
        assert ChatMessageItem.model_validate(dumped).connect == provider


def test_chat_message_item_rejects_an_unknown_connect_provider():
    from mandate_contracts.ddb import ChatMessageItem

    with pytest.raises(Exception):
        ChatMessageItem(id="1", ts="t", role="assistant", text="hi", connect="myspace")
