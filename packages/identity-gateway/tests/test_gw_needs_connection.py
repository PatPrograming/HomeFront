"""Lane I connection gate, lead's ruling: the gate runs only when something is actually sent.

Holding a draft never checks a connection. The check runs on auto-run (autonomy "auto" or a session
grant) and on send_draft (approve). Email goes through the staffer's connected Gmail, else the office
SES sender (HOMEFRONT_EMAIL_FROM, sent by the existing `email.send` handler), else "needs connection".
X goes through the connected X account, else "needs connection". Bluesky, LinkedIn and Facebook keep
share links and are never gated.

`identity_gateway.connections` is Lane I's module and is not merged yet, so these tests inject a
double with the agreed signatures through the gateway's module-level `connections` name (which is
None when the module is absent). SES is a fake client at the handler boundary; no real AWS.
"""

from __future__ import annotations

import types

import pytest
from mandate_contracts.ddb import AutonomyItem, CaseItem, EmailItem, StafferItem
from mcp_tools import handlers

NEEDS = "needs connection"
HELD = "held for staff review"
APPROVED = "approved by staff"
SEND_ARGS = {"to": "a@b.gov", "subject": "Hi", "body": "Hello"}
POST_ARGS = {"account": "official", "text": "Town hall Tuesday"}
REPLY_ARGS = {"account": "official", "reply_to": "https://x.com/a/status/1", "text": "Thanks", "platform": "x"}
OFFICE_SENDER = "office@example.gov"
GRANTED_AT = "2026-11-08T00:00:00Z"


class NeedsConnection(Exception):
    def __init__(self, provider):
        super().__init__(provider)
        self.provider = provider


class ProviderError(Exception):
    pass


class FakeSes:
    def __init__(self):
        self.sent: list[dict] = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "ses-123"}


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
def ses(monkeypatch):
    """A fake SES client at the handler boundary, and NO office sender unless `office_sender` is used."""
    fake = FakeSes()
    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: fake)
    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    return fake


@pytest.fixture
def office_sender(ses, monkeypatch):
    """The configured office SES sender: the same signal `email.send` uses to decide it can really send."""
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", OFFICE_SENDER)
    return OFFICE_SENDER


@pytest.fixture
def conn(monkeypatch, ses):
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


def _auto_email(store, world, how):
    """Email args that the agent may send alone: a case-status reply, via autonomy "auto" or a session grant."""
    store.put_email(
        "email-case",
        EmailItem(from_=world.known_email, subject="Any news?", body="Checking in.", kind="case_status"),
    )
    if how == "auto":
        store.put_autonomy("maya", "case_status", AutonomyItem(level="auto"))
    else:
        store.put_grant(world.sid, "case_status", "maya", GRANTED_AT, 2**31)
    return {"to": world.known_email, "subject": "Re", "body": "Update", "in_reply_to": "email-case"}


def _allow_social(store, world, how, category):
    if how == "auto":
        store.put_autonomy("maya", category, AutonomyItem(level="auto"))
    else:
        store.put_grant(world.sid, category, "maya", GRANTED_AT, 2**31)


def _chat(store, sid):
    return store.chat_since(sid, None)[0]


def _said(name, label):
    return f"I couldn't send this: {name} hasn't connected {label}. Nothing was sent."


def _approve(gw_, store, held, text=None):
    draft_id = held.result["draftId"]
    return draft_id, gw_.send_draft(draft_id, store.get_draft(draft_id), text or {})


# ---- holding a draft never needs a connection ---------------------------------------------------


@pytest.mark.parametrize(
    "tool,args",
    [("email_send", SEND_ARGS), ("social_post", POST_ARGS), ("social_reply", REPLY_ARGS)],
)
def test_holding_a_draft_needs_no_connection_and_posts_no_chat_message(
    gw_, store, make_token, world, poster, conn, handler_spy, ses, tool, args
):
    """Re-adding the hold-time guard would refuse to draft (and nag in the chat) for a staffer with no
    Gmail, no X and no office sender, though nothing is being sent yet."""
    conn.missing = {"gmail", "x"}
    out = gw_.guarded(tool, args, make_token())
    assert out.allowed and out.event.reason == HELD and out.result["status"] == "held_for_review"
    drafts = store.pending_drafts_for("maya")
    assert [d.tool for _, d in drafts] == [tool]
    assert conn.required == []  # the connection was not even asked about
    assert _chat(store, world.sid) == []
    assert conn.gmail == [] and conn.x == [] and ses.sent == [] and handler_spy.calls == []


def test_holding_the_list_email_for_the_chief_needs_no_connection(gw_, store, make_token, world, conn):
    """Same rule for the mailing list: re-adding the guard at hold time would block the chief's draft."""
    conn.missing = {"gmail"}
    out = gw_.guarded("email_send_list", {"subject": "s", "body": "b"}, make_token(act="chiefofstaff", sid=world.chief_sid))
    assert out.event.reason == HELD and len(store.pending_drafts_for("chiefofstaff")) == 1
    assert conn.required == [] and _chat(store, world.chief_sid) == []


@pytest.mark.parametrize("platform", ["bluesky", "linkedin", "facebook"])
def test_holding_a_share_link_post_is_unaffected(gw_, store, make_token, poster, conn, platform):
    """Share-link platforms were never gated; a guard that gated them would block a working path."""
    conn.missing = {"gmail", "x"}
    out = gw_.guarded("social_post", {**POST_ARGS, "platform": platform}, make_token())
    assert out.event.reason == HELD and conn.required == []


# ---- Cedar first, fail closed -------------------------------------------------------------------


def test_a_cedar_deny_comes_first_so_a_forbidden_call_never_asks_about_connections(gw_, store, make_token, world, conn, office_sender):
    """Without Cedar ordered before any connection check, an intern would learn to connect Gmail (or get an
    SES send) for a tool it may not use, even under autonomy auto."""
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    store.put_autonomy("maya", "new_conversation", AutonomyItem(level="auto"))
    conn.missing = {"gmail"}
    out = gw_.guarded("email_send", SEND_ARGS, make_token())
    assert out.event.reason == "denied by policy" and out.event.policy_ids == ["role-email-send"]
    assert conn.required == [] and conn.gmail == [] and _chat(store, world.sid) == []


def test_a_cedar_deny_posts_no_connect_message(gw_, store, make_token, world, conn):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    conn.missing = {"gmail"}
    gw_.guarded("email_send", SEND_ARGS, make_token())
    assert _chat(store, world.sid) == []


def test_approving_after_the_staffer_is_deactivated_denies_without_sending(gw_, store, make_token, conn, ses, office_sender):
    """Without the staffer check in send_draft, an offboarded staffer's draft would go out through the office SES sender."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    conn.required.clear()  # only what happens at approval counts
    conn.missing = {"gmail"}
    store.set_staffer_active("maya", False)
    _, out = _approve(gw_, store, held)
    assert out.allowed is False and out.event.reason == "session revoked / delegator inactive"
    assert conn.required == [] and conn.gmail == [] and ses.sent == []


def test_approving_after_the_role_lost_the_tool_denies_by_policy_without_sending(gw_, store, make_token, conn, ses, office_sender):
    """Without Cedar re-checked at approval, a demoted intern could still send through the office SES sender."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    conn.required.clear()  # only what happens at approval counts
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    conn.missing = {"gmail"}
    _, out = _approve(gw_, store, held)
    assert out.event.reason == "denied by policy" and out.event.policy_ids == ["role-email-send"]
    assert conn.required == [] and conn.gmail == [] and ses.sent == []


# ---- auto-run email: Gmail, else the office SES sender, else needs connection ----------------------


@pytest.mark.parametrize("how", ["auto", "grant"])
def test_an_auto_email_goes_through_the_connected_gmail_not_ses(
    gw_, store, make_token, world, conn, handler_spy, ses, office_sender, how
):
    """Without Gmail first, a connected staffer's own mail would be sent from the office address instead."""
    args = _auto_email(store, world, how)
    out = gw_.guarded("email_send", args, make_token())
    assert out.allowed and out.event.reason == "allowed"
    assert conn.gmail == [("maya", world.known_email, "Re", "Update", "email-case")]
    assert ses.sent == [] and "email.send" not in handler_spy.calls


@pytest.mark.parametrize("how", ["auto", "grant"])
def test_an_auto_email_with_no_gmail_but_an_office_sender_goes_through_ses(
    gw_, store, make_token, world, conn, handler_spy, ses, office_sender, how
):
    """Without the SES fallback, a staffer who never connected Gmail could not use auto-run in an office
    that has a configured sender."""
    conn.missing = {"gmail"}
    args = _auto_email(store, world, how)
    out = gw_.guarded("email_send", args, make_token())
    assert out.allowed and out.event.reason == "allowed"
    assert handler_spy.calls == ["email.send"]  # the existing SES handler ran
    assert [m["Destination"]["ToAddresses"] for m in ses.sent] == [[world.known_email]]
    assert out.result["provider"] == "ses" and out.result["status"] == "sent"
    assert conn.gmail == [] and _chat(store, world.sid) == []


@pytest.mark.parametrize("how", ["auto", "grant"])
def test_an_auto_email_with_neither_gmail_nor_office_sender_is_denied_needs_connection(
    gw_, store, events_of, make_token, world, conn, handler_spy, ses, how
):
    """Without the gate at send time, auto-run would call the SES handler with no sender and report a
    simulated 'send' as success, or send with no account."""
    conn.missing = {"gmail"}
    args = _auto_email(store, world, how)
    out = gw_.guarded("email_send", args, make_token())
    event = events_of(store)[-1]
    assert out.allowed is False and event.decision == "DENY" and event.reason == NEEDS
    assert out.result == _needs_connection_result(NeedsConnection("gmail"))
    assert event.atlas == []  # not an attack: no ATLAS deny labels
    assert [(m.role, m.text, m.connect) for m in _chat(store, world.sid)] == [("assistant", _said("maya", "Gmail"), "gmail")]
    assert conn.gmail == [] and ses.sent == [] and handler_spy.calls == []
    assert store.pending_drafts_for("maya") == []  # an auto-run call that cannot send makes no draft
    assert conn.required == [("maya", "gmail")]  # asked about the acting staffer


def test_the_needs_connection_message_goes_to_the_calling_session_only(gw_, store, make_token, world, conn):
    conn.missing = {"gmail"}
    args = _auto_email(store, world, "auto")
    gw_.guarded("email_send", args, make_token())
    assert len(_chat(store, world.sid)) == 1 and _chat(store, world.chief_sid) == []


# ---- auto-run social --------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["auto", "grant"])
def test_a_social_post_and_reply_go_out_through_x_when_allowed_alone(
    gw_, store, make_token, world, poster, conn, handler_spy, how
):
    _allow_social(store, world, how, "social_post")
    _allow_social(store, world, how, "social_reply")
    post = gw_.guarded("social_post", POST_ARGS, make_token())
    reply = gw_.guarded("social_reply", REPLY_ARGS, make_token())
    assert post.event.reason == "allowed" and reply.event.reason == "allowed"
    assert conn.x == [("maya", "Town hall Tuesday", None), ("maya", "Thanks", "https://x.com/a/status/1")]
    assert handler_spy.calls == [] and _chat(store, world.sid) == []


@pytest.mark.parametrize("how", ["auto", "grant"])
@pytest.mark.parametrize(
    "tool,args,category",
    [("social_post", POST_ARGS, "social_post"), ("social_reply", REPLY_ARGS, "social_reply")],
)
def test_an_auto_x_post_without_x_is_denied_needs_connection_with_a_connect_message(
    gw_, store, events_of, make_token, world, poster, conn, handler_spy, ses, office_sender, how, tool, args, category
):
    """Without the gate at send time, an auto X post with no X account would fall through to the share-link
    handler and report success. The office SES sender must not rescue X."""
    _allow_social(store, world, how, category)
    conn.missing = {"x"}
    out = gw_.guarded(tool, args, make_token())
    event = events_of(store)[-1]
    assert out.allowed is False and event.reason == NEEDS
    assert out.result == _needs_connection_result(NeedsConnection("x"))
    assert [(m.role, m.text, m.connect) for m in _chat(store, world.sid)] == [("assistant", _said("maya", "X"), "x")]
    assert conn.x == [] and ses.sent == [] and handler_spy.calls == []
    assert store.pending_drafts_for("maya") == [] and event.atlas == []


def test_a_social_post_with_no_platform_is_an_x_post_and_is_gated_when_sent(gw_, store, make_token, world, poster, conn):
    """A missing platform defaults to X, so sending it needs the X connection."""
    _allow_social(store, world, "auto", "social_post")
    conn.missing = {"x"}
    out = gw_.guarded("social_post", POST_ARGS, make_token())
    assert out.event.reason == NEEDS and out.result["provider"] == "x"


@pytest.mark.parametrize("platform", ["bluesky", "linkedin", "facebook"])
def test_platforms_without_a_connection_keep_the_share_link_handler_and_no_gate(
    gw_, store, make_token, world, poster, conn, handler_spy, platform
):
    """Bluesky, LinkedIn and Facebook stay on share links: gating them would block a working path."""
    _allow_social(store, world, "auto", "social_post")
    conn.missing = {"gmail", "x"}
    out = gw_.guarded("social_post", {**POST_ARGS, "platform": platform}, make_token())
    assert out.allowed and out.event.reason == "allowed"
    assert conn.required == [] and conn.x == [] and handler_spy.calls == ["social.post"]
    assert _chat(store, world.sid) == []


# ---- send_draft (approve): Gmail, else the office SES sender, else needs connection -------------------


def test_approving_an_email_sends_through_gmail_not_the_ses_handler(gw_, store, make_token, conn, handler_spy, ses, office_sender):
    held = gw_.guarded("email_send", {**SEND_ARGS, "in_reply_to": "email-1"}, make_token())
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert conn.gmail == [("maya", "a@b.gov", "Hi", "Hello", "email-1")]
    assert "email.send" not in handler_spy.calls and ses.sent == []


def test_approving_a_list_email_sends_one_gmail_per_dynamodb_recipient(gw_, store, make_token, world, conn, handler_spy):
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    held = gw_.guarded("email_send_list", {"subject": "News", "body": "Hello", "recipients": ["evil@evil.com"]}, token)
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert [(c[0], c[1], c[2], c[3]) for c in conn.gmail] == [("chiefofstaff", "jane.doe@example.com", "News", "Hello")]
    assert "email.send_list" not in handler_spy.calls


def test_approving_an_email_with_no_gmail_but_an_office_sender_sends_through_ses(
    gw_, store, make_token, conn, handler_spy, ses, office_sender
):
    """Without the SES fallback on approve, a staffer with no Gmail could never send an approved draft
    from an office that has a configured sender."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    conn.missing = {"gmail"}  # never connected, or disconnected since the hold
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert handler_spy.calls == ["email.send"]
    assert [m["Destination"]["ToAddresses"] for m in ses.sent] == [["a@b.gov"]]
    assert out.result["provider"] == "ses" and conn.gmail == []
    assert store.get_draft(draft_id).status == "sent"


def test_approving_a_list_email_with_no_gmail_but_an_office_sender_sends_through_ses(
    gw_, store, make_token, world, conn, handler_spy, ses, office_sender
):
    """The list email gets the same fallback, to the DynamoDB recipients (never the agent's)."""
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    held = gw_.guarded("email_send_list", {"subject": "News", "body": "Hello", "recipients": ["evil@evil.com"]}, token)
    conn.missing = {"gmail"}
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert handler_spy.calls == ["email.send_list"]
    assert [m["Destination"]["ToAddresses"] for m in ses.sent] == [["jane.doe@example.com"]]
    assert conn.gmail == []


def test_approving_an_email_with_neither_gmail_nor_office_sender_returns_the_draft_to_pending(
    gw_, store, make_token, world, conn, handler_spy, ses
):
    """Without the gate at approval, the SES handler would 'simulate' a send and the draft would be marked sent."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    conn.missing = {"gmail"}
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed is False and out.event.reason == NEEDS
    assert out.result == _needs_connection_result(NeedsConnection("gmail"))
    assert conn.gmail == [] and ses.sent == [] and handler_spy.calls == []
    assert store.get_draft(draft_id).status == "pending"
    assert [(m.role, m.text, m.connect) for m in _chat(store, world.sid)] == [("assistant", _said("maya", "Gmail"), "gmail")]


def test_approving_a_list_email_with_neither_posts_the_chiefs_name(gw_, store, make_token, world, conn, ses):
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    held = gw_.guarded("email_send_list", {"subject": "s", "body": "b"}, token)
    conn.missing = {"gmail"}
    draft_id, out = _approve(gw_, store, held)
    assert out.event.reason == NEEDS and store.get_draft(draft_id).status == "pending" and ses.sent == []
    assert [(m.text, m.connect) for m in _chat(store, world.chief_sid)] == [(_said("chiefofstaff", "Gmail"), "gmail")]


def test_approving_an_x_post_sends_through_x(gw_, store, make_token, poster, conn, handler_spy):
    held = gw_.guarded("social_reply", REPLY_ARGS, make_token())
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert conn.x == [("maya", "Thanks", "https://x.com/a/status/1")] and handler_spy.calls == []


def test_approving_an_x_post_without_x_hands_off_to_x_compose_with_the_text(
    gw_, store, make_token, world, poster, conn, handler_spy, ses, office_sender
):
    """No X account connected: the approval opens X's compose page with the text, and the staffer presses Post."""
    held = gw_.guarded("social_post", POST_ARGS, make_token())
    conn.missing = {"x"}
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed and out.event.decision == "ALLOW" and out.event.reason == "handed off to X"
    assert out.result["status"] == "share_link"
    assert out.result["url"] == "https://x.com/intent/post?text=Town%20hall%20Tuesday"
    assert conn.x == [] and ses.sent == [] and handler_spy.calls == []
    assert store.get_draft(draft_id).status == "sent"
    assert _chat(store, world.sid) == []


def test_a_denied_x_post_without_x_is_denied_and_gets_no_share_link(gw_, store, make_token, world, poster, conn):
    held = gw_.guarded("social_post", POST_ARGS, make_token())
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="intern"))
    conn.missing = {"x"}
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed is False and out.event.decision == "DENY" and out.event.reason == "denied by policy"
    assert not out.result or "url" not in out.result
    assert store.get_draft(draft_id).status == "pending"


def test_approving_a_share_link_post_is_never_gated(gw_, store, make_token, world, poster, conn, handler_spy):
    held = gw_.guarded("social_post", {**POST_ARGS, "platform": "bluesky"}, make_token())
    conn.missing = {"gmail", "x"}
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert conn.required == [] and handler_spy.calls == ["social.post"] and _chat(store, world.sid) == []


# ---- errors deny and the draft stays reviewable ------------------------------------------------------


def test_a_sender_that_raises_needs_connection_gets_the_needs_connection_deny(gw_, store, make_token, world, conn):
    """A token that expired between the check and the send is a connection problem, not a crash."""
    held = gw_.guarded("email_send", SEND_ARGS, make_token())

    def expired(*a, **k):
        raise NeedsConnection("gmail")

    conn.send_gmail = expired
    draft_id, out = _approve(gw_, store, held)
    assert out.event.reason == NEEDS and out.result["status"] == "needs_connection"
    assert store.get_draft(draft_id).status == "pending"
    assert [(m.text, m.connect) for m in _chat(store, world.sid)] == [(_said("maya", "Gmail"), "gmail")]


def test_a_provider_error_on_approval_is_a_deny_and_the_draft_stays_pending(gw_, store, make_token, conn):
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    conn.send_error = ProviderError("gmail said 500")
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed is False and out.event.reason == "denied by policy" and out.result is None
    assert store.get_draft(draft_id).status == "pending"


def test_a_provider_error_on_a_direct_send_is_a_deny(gw_, store, make_token, world, poster, conn):
    _allow_social(store, world, "auto", "social_post")
    conn.send_error = ProviderError("x said 429")
    out = gw_.guarded("social_post", POST_ARGS, make_token())
    assert out.allowed is False and out.event.reason == "denied by policy" and out.result is None


# ---- a list send that fails part-way must never send anyone a second copy ---------------------------


def _three_recipient_list(store, make_token, world, gw_):
    """The chief's held list email, with three DynamoDB recipients (sorted: amy, jane.doe, zed)."""
    for case_id, email in (("case-amy", "amy@example.com"), ("case-zed", "zed@example.com")):
        store.put_case(
            case_id, CaseItem(office="rep-x", constituent=email, constituent_email=email, topic="t", status="open")
        )
    token = make_token(act="chiefofstaff", sid=world.chief_sid)
    return gw_.guarded("email_send_list", {"subject": "News", "body": "Hello"}, token)


def _fail_on_call(conn, n):
    """Make the Gmail double raise ProviderError on its nth call (1-based); earlier calls are recorded."""
    calls = []

    def send_gmail(staffer_id, to, subject, body, in_reply_to=None):
        calls.append(to)
        if len(calls) == n:
            raise ProviderError("gmail said 500")
        conn.gmail.append((staffer_id, to, subject, body, in_reply_to))
        return {"status": "sent", "provider": "gmail"}

    conn.send_gmail = send_gmail
    return calls


def test_a_list_send_failing_on_the_second_recipient_is_a_partial_failure_and_the_draft_stays_sent(
    gw_, store, make_token, world, conn
):
    """Without partial-failure handling the error would put the draft back to pending, and a retry would
    email the first recipient again."""
    held = _three_recipient_list(store, make_token, world, gw_)
    _fail_on_call(conn, 2)
    draft_id, out = _approve(gw_, store, held)
    assert out.result == {
        "status": "partial_failure",
        "provider": "gmail",
        "sent": 1,
        "failed": ["jane.doe@example.com", "zed@example.com"],
    }
    assert [c[1] for c in conn.gmail] == ["amy@example.com"]
    assert store.get_draft(draft_id).status == "sent"
    assert store.pending_drafts_for("chiefofstaff") == []


def test_approving_a_partially_sent_list_again_is_refused_so_nobody_gets_a_second_copy(
    gw_, store, make_token, world, conn
):
    from identity_gateway.gateway import DraftAlreadyDecided

    held = _three_recipient_list(store, make_token, world, gw_)
    _fail_on_call(conn, 2)
    draft_id, _ = _approve(gw_, store, held)
    conn.send_gmail = lambda *a, **k: pytest.fail("a second approval must not send anything")
    with pytest.raises(DraftAlreadyDecided):
        gw_.send_draft(draft_id, store.get_draft(draft_id), {})
    assert [c[1] for c in conn.gmail] == ["amy@example.com"]


def test_a_list_send_failing_on_the_first_recipient_returns_the_draft_to_pending_with_nothing_sent(
    gw_, store, make_token, world, conn
):
    """Nobody has the email yet, so a retry is safe and the draft must stay reviewable."""
    held = _three_recipient_list(store, make_token, world, gw_)
    calls = _fail_on_call(conn, 1)
    draft_id, out = _approve(gw_, store, held)
    assert out.allowed is False and out.result is None
    assert calls == ["amy@example.com"] and conn.gmail == []
    assert store.get_draft(draft_id).status == "pending"
    assert [i for i, _ in store.pending_drafts_for("chiefofstaff")] == [draft_id]


# ---- connected sends post no connect message ------------------------------------------------------


def test_a_connected_send_posts_no_connect_message(gw_, store, make_token, world, poster, conn):
    _allow_social(store, world, "auto", "social_post")
    gw_.guarded("email_send", SEND_ARGS, make_token())  # held
    gw_.guarded("social_post", POST_ARGS, make_token())  # sent through X
    assert _chat(store, world.sid) == []


def test_a_connected_approval_posts_no_connect_message(gw_, store, make_token, world, conn):
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    _approve(gw_, store, held)
    assert _chat(store, world.sid) == []


# ---- module absent: exactly as today ----------------------------------------------------------


def test_with_the_module_absent_outbound_calls_behave_as_today(gw_, store, make_token, monkeypatch, handler_spy, ses):
    """The gateway must run without Lane I: held, then the SES handler on approval."""
    import identity_gateway.gateway as gw

    monkeypatch.setattr(gw, "connections", None, raising=False)
    held = gw_.guarded("email_send", SEND_ARGS, make_token())
    assert held.event.reason == HELD
    _, out = _approve(gw_, store, held)
    assert out.allowed and out.event.reason == APPROVED and handler_spy.calls == ["email.send"]


# ---- the chat item -------------------------------------------------------------------------------


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
