"""The Emails form -> the chat agent (moto DynamoDB, no AWS, no Cognito, no real agent process)."""

from __future__ import annotations

import pytest

from app.routers.agent_requests import EmailRequestIn, build_brief

from .test_assistant_api import SID, agent_headers, as_staff, client, gateway, session, store, table  # noqa: F401 (fixtures)
from .test_gateway_api import CHIEF

URL = f"/api/v1/sessions/{SID}/agent-requests"

FORM = {
    "type": "email",
    "role": "Send",
    "message": "Benefits enrollment closes Friday, 5 pm.",
    "restrictions": "Never promise an extension.",
    "recipients": ["Press@Agency.gov", "press@agency.gov", " jane.doe@example.com "],
    "mailboxes": ["Public inbox"],
    "mail_platforms": ["Google Workspace"],
    "most_common_mail_type": "FAQs",
    "emails_per_day": 40,
}


def test_the_form_reaches_the_agent_as_a_chat_message(client, session):
    as_staff()
    sent = client.post(URL, json=FORM)
    assert sent.status_code == 200
    assert sent.json()["recipients"] == ["press@agency.gov", "jane.doe@example.com"]  # trimmed, lower-cased, de-duplicated

    inbox = client.get("/api/v1/agent/messages", headers=agent_headers()).json()["messages"]
    assert [m["role"] for m in inbox] == ["user"]
    text = inbox[0]["text"]
    assert "email_send" in text and "press@agency.gov, jane.doe@example.com" in text
    assert "> Benefits enrollment closes Friday, 5 pm." in text
    assert "> Never promise an extension." in text


@pytest.mark.parametrize(
    ("role", "says", "never"),
    [
        ("Send", "send it with email_send", None),
        ("Draft", "Do not call email_send", "send it with email_send"),
        ("Prioritize", "inbox_read", "send it with email_send"),
        ("Only summarize", "summarize", "send it with email_send"),
    ],
)
def test_each_action_tells_the_agent_what_to_do(role, says, never):
    brief = build_brief(EmailRequestIn(**{**FORM, "role": role}), "maya")
    assert says in brief
    if never:
        assert never not in brief


def test_send_and_draft_need_a_recipient_but_reading_does_not(client, session):
    as_staff()
    assert client.post(URL, json={**FORM, "recipients": []}).status_code == 422
    assert client.post(URL, json={**FORM, "role": "Draft", "recipients": []}).status_code == 422
    assert client.post(URL, json={**FORM, "role": "Prioritize", "recipients": []}).status_code == 200


@pytest.mark.parametrize("bad", ["not-an-address", "a@b", "two words@x.gov", "<x@y.gov>", "a@b.gov, c@d.gov"])
def test_a_malformed_recipient_is_refused(client, session, bad):
    as_staff()
    assert client.post(URL, json={**FORM, "recipients": [bad]}).status_code == 422


def test_an_outside_address_is_not_refused_here_so_cedar_can_deny_it_visibly(client, session):
    as_staff()
    assert client.post(URL, json={**FORM, "recipients": ["someone@gmail.com"]}).status_code == 200


def test_only_the_social_form_type_is_not_accepted_yet(client, session):
    as_staff()
    assert client.post(URL, json={**FORM, "type": "social_post"}).status_code == 422


def test_an_empty_message_is_refused(client, session):
    as_staff()
    assert client.post(URL, json={**FORM, "message": "   "}).status_code == 422


def test_another_staffer_cannot_post_into_mayas_session(client, session):
    as_staff(CHIEF)
    assert client.post(URL, json=FORM).status_code == 404


def test_a_revoked_session_takes_no_request(client, session, store):
    store.set_staffer_active("maya", False)
    as_staff()
    assert client.post(URL, json=FORM).status_code == 403
