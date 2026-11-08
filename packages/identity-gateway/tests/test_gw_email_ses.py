"""email_send over SES (main's email support): real sends only after the Cedar ALLOW, fail closed."""

from __future__ import annotations

import json

import pytest
from mcp_tools import handlers

# A reply to a case-status email, a category maya lets the agent send alone (`trusted_replies`).
ARGS = {"subject": "Hi", "body": "Update", "in_reply_to": "email-trusted"}


class FakeSes:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    def send_email(self, **kwargs):
        if self.fail:
            raise RuntimeError("SES throttled")
        self.sent.append(kwargs)
        return {"MessageId": "ses-123"}


@pytest.fixture
def ses(monkeypatch):
    fake = FakeSes()
    monkeypatch.setattr(handlers, "_ses_client", lambda: fake)
    return fake


def test_without_a_sender_configured_the_send_is_simulated(trusted_replies, gateway, make_token, world, ses, monkeypatch):
    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    out = gateway.guarded("email_send", {"to": world.known_email, **ARGS}, make_token())
    assert out.allowed is True
    assert out.result["simulated"] is True and out.result["status"] == "simulated"
    assert ses.sent == []


def test_with_a_sender_an_allowed_send_goes_through_ses(trusted_replies, gateway, make_token, world, ses, monkeypatch):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "agency@example.gov")
    out = gateway.guarded("email_send", {"to": world.known_email, **ARGS}, make_token())
    assert out.allowed is True
    assert out.result["simulated"] is False and out.result["message_id"] == "ses-123"
    assert [m["Destination"]["ToAddresses"] for m in ses.sent] == [[world.known_email]]
    assert "AWS_" not in json.dumps(out.result)


def test_a_denied_send_never_reaches_ses(gateway, make_token, ses, monkeypatch):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "agency@example.gov")
    out = gateway.guarded("email_send", {"to": "stranger@gmail.com", **ARGS}, make_token())
    assert out.allowed is False and out.result is None
    assert ses.sent == []


def test_an_ses_failure_is_a_deny_not_a_fake_success(trusted_replies, gateway, make_token, world, monkeypatch):
    monkeypatch.setenv("HOMEFRONT_EMAIL_FROM", "agency@example.gov")
    monkeypatch.setattr(handlers, "_ses_client", lambda: FakeSes(fail=True))
    out = gateway.guarded("email_send", {"to": world.known_email, **ARGS}, make_token())
    assert out.event.decision == "DENY" and out.result is None
