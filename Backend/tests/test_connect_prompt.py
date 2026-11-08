"""Approving a held draft whose Gmail is not connected: the chat says so and offers Connect.

The Lane I `connections` module is replaced by a double, the same way the gateway tests do.
"""

from __future__ import annotations

import types

import pytest

from .test_assistant_api import CASE_EMAIL, SID, as_staff, hold_reply, session  # noqa: F401 (fixtures)
from .test_gateway_api import _env, client, gateway, store, table  # noqa: F401 (fixtures)


class NeedsConnection(Exception):
    def __init__(self, provider):
        super().__init__(provider)
        self.provider = provider


@pytest.fixture
def conn(monkeypatch):
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


def test_approving_with_gmail_disconnected_returns_needs_connection_and_keeps_the_draft(client, session, gateway, conn):
    draft_id = hold_reply(gateway)
    conn.missing = {"gmail"}
    as_staff()
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={})
    assert response.status_code == 200
    # pinned by Lane I: the draft id plus the connection result, exactly
    assert response.json() == {"id": draft_id, **conn.needs_connection_result(NeedsConnection("gmail"))}
    assert response.json()["status"] == "needs_connection" and response.json()["provider"] == "gmail"
    assert [d["id"] for d in client.get("/api/v1/drafts").json()["drafts"]] == [draft_id]
    assert conn.sent == []


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
