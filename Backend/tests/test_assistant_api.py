"""Chat console, review queue and earned autonomy routes (moto DynamoDB, no AWS, no Cognito)."""

from __future__ import annotations

import time

import pytest
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import MandateClaims
from mandate_contracts.ddb import EmailItem, SessionItem

from app.main import app
from app.routers import assistant

from .test_gateway_api import CHIEF, MAYA, SECRET, _env, client, gateway, store, table  # noqa: F401 (fixtures)

SID = "sid-chat-1"
CASE_EMAIL = "email-7"  # a case-status email from jane.doe, a constituent on file


@pytest.fixture
def session(store):
    store.put_session(
        SID,
        SessionItem(status="active", agent="agent-1", acting_for="maya", office="rep-x", mode="official",
                    created_at="2026-11-07T20:00:00+00:00"),
    )
    store.put_email(
        CASE_EMAIL,
        EmailItem(from_="jane.doe@example.com", subject="Pothole?", body="Any news?", kind="case_status"),
    )
    return SID


def as_staff(identity=MAYA):
    app.dependency_overrides[assistant.require_staff] = lambda: identity


def agent_headers(sid=SID):
    now = int(time.time())
    claims = MandateClaims(sub="agent-1", act="maya", office="rep-x", mode="official", sid=sid, iat=now, exp=now + 900)
    return {"Authorization": f"Bearer {mint_mandate_token(claims, SECRET)}"}


def hold_reply(gateway, body="We called DPW today."):
    """The agent replies to the case-status email; the gateway holds it and returns the draft id."""
    args = {"to": "jane.doe@example.com", "subject": "Re: Pothole?", "body": body, "in_reply_to": CASE_EMAIL}
    token = agent_headers()["Authorization"].removeprefix("Bearer ")
    return gateway.guarded("email_send", args, token).result["draftId"]


# --- chat ---------------------------------------------------------------------


def test_staffer_and_agent_chat_through_the_session(client, session):
    as_staff()
    sent = client.post(f"/api/v1/sessions/{SID}/messages", json={"text": "Check my email"})
    assert sent.status_code == 200 and sent.json()["role"] == "user"

    inbox = client.get("/api/v1/agent/messages", headers=agent_headers()).json()
    assert [m["text"] for m in inbox["messages"]] == ["Check my email"]

    client.post("/api/v1/agent/messages", json={"text": "Done: 2 drafts for you."}, headers=agent_headers())
    newer = client.get(f"/api/v1/sessions/{SID}/messages", params={"after": inbox["next"]}).json()
    assert [(m["role"], m["text"]) for m in newer["messages"]] == [("assistant", "Done: 2 drafts for you.")]


def test_agent_chat_needs_a_valid_mandate_token(client, session):
    assert client.get("/api/v1/agent/messages").status_code == 401
    assert client.get("/api/v1/agent/messages", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_offboarding_cuts_the_agent_out_of_the_chat(client, session, store):
    store.set_staffer_active("maya", False)
    assert client.get("/api/v1/agent/messages", headers=agent_headers()).status_code == 403
    assert client.post("/api/v1/agent/messages", json={"text": "hi"}, headers=agent_headers()).status_code == 403


def test_another_staffer_cannot_read_or_post_in_mayas_chat(client, session):
    as_staff(CHIEF)
    assert client.get(f"/api/v1/sessions/{SID}/messages").status_code == 404
    assert client.post(f"/api/v1/sessions/{SID}/messages", json={"text": "hi"}).status_code == 404


# --- review queue -------------------------------------------------------------


def test_held_drafts_are_listed_and_approved_with_new_wording(client, session, gateway):
    draft_id = hold_reply(gateway)
    as_staff()
    drafts = client.get("/api/v1/drafts").json()["drafts"]
    assert [(d["id"], d["category"], d["categoryLabel"]) for d in drafts] == [(draft_id, "case_status", "Case status updates")]

    approved = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"body": "Better wording"})
    assert approved.json() == {"id": draft_id, "status": "sent", "edited": True}
    assert client.get("/api/v1/drafts").json()["drafts"] == []
    assert client.post(f"/api/v1/drafts/{draft_id}/approve", json={}).status_code == 409


def test_the_recipient_cannot_be_changed_on_approval(client, session, gateway):
    draft_id = hold_reply(gateway)
    as_staff()
    assert client.post(f"/api/v1/drafts/{draft_id}/approve", json={"to": "x@evil.example"}).status_code == 422


def test_only_the_drafts_staffer_can_decide_it(client, session, gateway):
    draft_id = hold_reply(gateway)
    as_staff(CHIEF)
    assert client.post(f"/api/v1/drafts/{draft_id}/approve", json={}).status_code == 404
    assert client.post(f"/api/v1/drafts/{draft_id}/reject").status_code == 404


# --- earned autonomy ------------------------------------------------------------


def _category(client, name):
    return next(c for c in client.get("/api/v1/autonomy").json()["categories"] if c["category"] == name)


def test_three_unchanged_approvals_offer_autonomy_and_accepting_it_sends_directly(client, session, gateway, store):
    as_staff()
    for _ in range(3):
        client.post(f"/api/v1/drafts/{hold_reply(gateway)}/approve", json={})
    offer = _category(client, "case_status")
    assert (offer["approvedUnchanged"], offer["suggestAuto"]) == (3, True)

    assert client.put("/api/v1/autonomy/case_status", json={"level": "auto"}).json()["level"] == "auto"
    token = agent_headers()["Authorization"].removeprefix("Bearer ")
    args = {"to": "jane.doe@example.com", "subject": "Re", "body": "Update", "in_reply_to": CASE_EMAIL}
    assert gateway.guarded("email_send", args, token).event.reason == "allowed"


def test_a_rejection_counts_against_the_offer(client, session, gateway):
    as_staff()
    for _ in range(3):
        client.post(f"/api/v1/drafts/{hold_reply(gateway)}/approve", json={})
    client.post(f"/api/v1/drafts/{hold_reply(gateway)}/reject")
    offer = _category(client, "case_status")
    assert (offer["rejected"], offer["suggestAuto"]) == (1, False)


def test_not_now_hides_the_offer(client, session, gateway):
    as_staff()
    for _ in range(3):
        client.post(f"/api/v1/drafts/{hold_reply(gateway)}/approve", json={})
    assert client.post("/api/v1/autonomy/case_status/snooze").json()["suggestAuto"] is False


def test_locked_categories_cannot_be_set_to_auto(client, session):
    as_staff()
    assert client.put("/api/v1/autonomy/sensitive", json={"level": "auto"}).status_code == 403
    assert client.put("/api/v1/autonomy/no_such_thing", json={"level": "auto"}).status_code == 404
    assert _category(client, "sensitive")["locked"] is True
