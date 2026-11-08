"""Approving a held draft: Gmail, else the office SES sender, else the chat offers Connect.

Holding a draft never needs a connection (lead's ruling); the check runs when the approve click sends.
The Lane I `connections` module is replaced by a double, the same way the gateway tests do, and SES is
a fake client at the handler boundary (no real AWS).
"""

from __future__ import annotations

import types

import pytest
from mcp_tools import handlers

from .test_assistant_api import CASE_EMAIL, SID, as_staff, hold_reply, session  # noqa: F401 (fixtures)
from .test_gateway_api import _env, client, gateway, store, table  # noqa: F401 (fixtures)


class NeedsConnection(Exception):
    def __init__(self, provider):
        super().__init__(provider)
        self.provider = provider


class FakeSes:
    def __init__(self):
        self.sent: list[dict] = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "ses-123"}


@pytest.fixture
def ses(monkeypatch):
    """A fake SES client at the handler boundary; no office sender unless `office_sender` is used."""
    fake = FakeSes()
    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: fake)
    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    return fake


@pytest.fixture
def office_sender(ses, monkeypatch):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "office@example.gov")


@pytest.fixture
def conn(monkeypatch, ses):
    import identity_gateway.gateway as gw

    d = types.ModuleType("identity_gateway.connections")
    d.NeedsConnection = NeedsConnection
    d.missing = set()
    d.sent = []
    d.provider_for_tool = lambda tool, args: "gmail" if tool in ("email_send", "email_send_list") else None

    def require_connection(staffer_id, provider):
        if provider in d.missing:
            raise NeedsConnection(provider)

    def send_gmail(staffer_id, to, subject, body, in_reply_to=None):
        d.sent.append((staffer_id, to))
        return {"status": "sent", "provider": "gmail"}

    d.require_connection, d.send_gmail = require_connection, send_gmail
    d.needs_connection_result = lambda exc: {
        "status": "needs_connection",
        "provider": exc.provider,
        "label": "Gmail",
        "connectUrl": f"/account?connect={exc.provider}",
        "note": "NOT sent.",
    }
    monkeypatch.setattr(gw, "connections", d, raising=False)
    return d


def _messages(client):
    return client.get(f"/api/v1/sessions/{SID}/messages").json()["messages"]


def _pending_ids(client):
    return [d["id"] for d in client.get("/api/v1/drafts").json()["drafts"]]


def test_holding_a_reply_with_gmail_disconnected_still_makes_the_draft_and_posts_nothing(client, session, gateway, conn):
    """Re-adding the hold-time guard would refuse the draft and post a Connect message before anything is sent."""
    conn.missing = {"gmail"}
    as_staff()
    draft_id = hold_reply(gateway)
    assert _pending_ids(client) == [draft_id]
    assert _messages(client) == []


def test_approving_with_no_gmail_and_no_office_sender_returns_needs_connection_and_keeps_the_draft(
    client, session, gateway, conn, ses
):
    """Without the gate at approval, the SES handler would simulate a send and the draft would leave the queue."""
    draft_id = hold_reply(gateway)
    conn.missing = {"gmail"}
    as_staff()
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={})
    assert response.status_code == 200
    # pinned by Lane I: the draft id plus the connection result, exactly
    assert response.json() == {"id": draft_id, **conn.needs_connection_result(NeedsConnection("gmail"))}
    assert response.json()["status"] == "needs_connection" and response.json()["provider"] == "gmail"
    assert _pending_ids(client) == [draft_id]
    assert conn.sent == [] and ses.sent == []


def test_the_chat_then_shows_the_message_with_a_gmail_connect_button(client, session, gateway, conn):
    """Without the server-side post, the chat stays silent after the failed approval."""
    draft_id = hold_reply(gateway)
    conn.missing = {"gmail"}
    as_staff()
    client.post(f"/api/v1/drafts/{draft_id}/approve", json={})
    messages = _messages(client)
    assert [(m["role"], m["text"], m["connect"]) for m in messages] == [
        ("assistant", "I couldn't send this: maya hasn't connected Gmail. Nothing was sent.", "gmail")
    ]


def test_approving_with_no_gmail_but_an_office_sender_sends_through_ses(client, session, gateway, conn, ses, office_sender):
    """Without the SES fallback, a staffer with no Gmail could not approve anything in an office with a sender."""
    draft_id = hold_reply(gateway)
    conn.missing = {"gmail"}
    as_staff()
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={})
    assert response.status_code == 200 and response.json()["status"] == "sent"
    assert [m["Destination"]["ToAddresses"] for m in ses.sent] == [["jane.doe@example.com"]]
    assert conn.sent == []
    assert _pending_ids(client) == []
    assert _messages(client) == []  # no Connect prompt when the send worked


def test_approving_with_gmail_connected_uses_gmail_even_with_an_office_sender(client, session, gateway, conn, ses, office_sender):
    draft_id = hold_reply(gateway)
    as_staff()
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={})
    assert response.status_code == 200 and response.json()["status"] == "sent"
    assert conn.sent == [("maya", "jane.doe@example.com")] and ses.sent == []


def test_the_draft_can_be_approved_after_connecting(client, session, gateway, conn):
    draft_id = hold_reply(gateway)
    conn.missing = {"gmail"}
    as_staff()
    client.post(f"/api/v1/drafts/{draft_id}/approve", json={})
    conn.missing = set()
    again = client.post(f"/api/v1/drafts/{draft_id}/approve", json={})
    assert again.status_code == 200 and again.json()["status"] == "sent" and conn.sent == [("maya", "jane.doe@example.com")]


def test_normal_chat_messages_have_no_connect_value(client, session, gateway, conn):
    as_staff()
    client.post(f"/api/v1/sessions/{SID}/messages", json={"text": "hello"})
    draft_id = hold_reply(gateway)
    client.post(f"/api/v1/drafts/{draft_id}/approve", json={})  # connected: sent
    messages = _messages(client)
    assert messages and all(m.get("connect") is None for m in messages)
    assert len(messages) == 1
