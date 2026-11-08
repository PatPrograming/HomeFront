"""The connection gate with Lane I's REAL `identity_gateway.connections`, not the double.

test_gw_needs_connection.py pins the lead's ruling against a double with the agreed signatures. These
tests run the same ruling through Lane I's actual ConnectionService once both branches are merged: the
service gets this test's moto store (not the global `get_gateway()`), an identity cipher instead of KMS,
and provider HTTP through httpx.MockTransport. No real AWS, Google or X.

Before the merge the module does not exist on this branch, so the whole file is skipped (importorskip).
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
from mandate_contracts.ddb import StafferItem
from mcp_tools import handlers

connections = pytest.importorskip("identity_gateway.connections")

NEEDS = "needs connection"
APPROVED = "approved by staff"
SEND_ARGS = {"to": "a@b.gov", "subject": "Hi", "body": "Hello"}
POST_ARGS = {"account": "official", "text": "Town hall Tuesday"}
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
X_TWEETS = "https://api.x.com/2/tweets"


class PlainCipher:
    """Identity cipher: lets a test seed a connection row without KMS."""

    def encrypt(self, plaintext, context):
        return plaintext

    def decrypt(self, ciphertext, context):
        return ciphertext


class FakeSes:
    def __init__(self):
        self.sent: list[dict] = []

    def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "ses-123"}


@pytest.fixture
def provider():
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        url = str(request.url)
        if url == GMAIL_SEND:
            return httpx.Response(200, json={"id": "gm-1"})
        if url == X_TWEETS:
            return httpx.Response(201, json={"data": {"id": "tw-1"}})
        return httpx.Response(500, json={"error": f"unexpected {url}"})

    return requests, httpx.Client(transport=httpx.MockTransport(answer))


@pytest.fixture
def real(monkeypatch, store, provider):
    """Lane I's real module on the gateway, bound to this test's store."""
    import identity_gateway.gateway as gw

    _, http = provider
    config = connections.OAuthConfig(kms_key_id="", redirect_uri="http://localhost/cb", clients={"gmail": ("", ""), "x": ("", "")})
    service = connections.ConnectionService(store, PlainCipher(), http, config)
    connections.set_connections(service)
    monkeypatch.setattr(gw, "connections", connections, raising=False)
    yield service
    connections.set_connections(None)


@pytest.fixture
def ses(monkeypatch):
    fake = FakeSes()
    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: fake)
    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    return fake


@pytest.fixture
def poster(store):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="communications_director"))


def _connect(service, store, staffer, provider):
    tokens = {"access_token": f"tok-{provider}", "refresh_token": "r", "expires_at": time.time() + 3600}
    store.table.put_item(Item={
        "pk": f"CONNECTION#{staffer}", "sk": f"PROVIDER#{provider}",
        "handle": f"{staffer}@{provider}", "connected_at": "2026-11-08T00:00:00+00:00", "scopes": "send",
        "ciphertext": base64.b64encode(json.dumps(tokens).encode()).decode(),
    })


def _approve(gw_, store, held):
    draft_id = held.result["draftId"]
    return draft_id, gw_.send_draft(draft_id, store.get_draft(draft_id), {})


def test_no_gmail_and_no_office_sender_keeps_the_draft_pending(engine_gateway, store, make_token, real, ses, provider):
    held = engine_gateway.guarded("email_send", SEND_ARGS, make_token())
    draft_id, out = _approve(engine_gateway, store, held)
    assert out.allowed is False and out.event.reason == NEEDS
    assert out.result["status"] == "needs_connection" and out.result["provider"] == "gmail"
    assert ses.sent == [] and provider[0] == [] and store.get_draft(draft_id).status == "pending"


def test_no_gmail_with_an_office_sender_sends_through_ses(engine_gateway, store, make_token, real, ses, monkeypatch, provider):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "office@example.gov")
    held = engine_gateway.guarded("email_send", SEND_ARGS, make_token())
    draft_id, out = _approve(engine_gateway, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert [m["Destination"]["ToAddresses"] for m in ses.sent] == [["a@b.gov"]] and provider[0] == []
    assert store.get_draft(draft_id).status == "sent"


def test_a_connected_gmail_sends_through_gmail_not_ses(engine_gateway, store, make_token, real, ses, monkeypatch, provider):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "office@example.gov")
    _connect(real, store, "maya", "gmail")
    held = engine_gateway.guarded("email_send", SEND_ARGS, make_token())
    _, out = _approve(engine_gateway, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert [str(r.url) for r in provider[0]] == [GMAIL_SEND] and ses.sent == []
    assert provider[0][0].headers["Authorization"] == "Bearer tok-gmail"


def test_an_x_post_without_x_keeps_the_draft_pending(engine_gateway, store, make_token, real, poster, provider):
    held = engine_gateway.guarded("social_post", POST_ARGS, make_token())
    draft_id, out = _approve(engine_gateway, store, held)
    assert out.event.reason == NEEDS and out.result["provider"] == "x"
    assert provider[0] == [] and store.get_draft(draft_id).status == "pending"


def test_a_connected_x_posts_through_x(engine_gateway, store, make_token, real, poster, provider):
    _connect(real, store, "maya", "x")
    held = engine_gateway.guarded("social_post", POST_ARGS, make_token())
    _, out = _approve(engine_gateway, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert [str(r.url) for r in provider[0]] == [X_TWEETS]


def test_a_bluesky_post_is_never_gated(engine_gateway, store, make_token, real, poster, handler_spy, provider):
    held = engine_gateway.guarded("social_post", {**POST_ARGS, "platform": "bluesky"}, make_token())
    _, out = _approve(engine_gateway, store, held)
    assert out.allowed and out.event.reason == APPROVED
    assert handler_spy.calls == ["social.post"] and provider[0] == []
