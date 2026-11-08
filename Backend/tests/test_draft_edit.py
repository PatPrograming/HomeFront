"""Staffer draft-edit API (check C12): GET /drafts fields, PATCH /drafts/{id}, expectedVersion, and the
escalation rules (an edit never skips the approval-time re-check).

Everything goes through the real route stack and the real gateway (moto DynamoDB, the starter Cedar
policies). Only the Lane I `connections` module is a double, as in test_connect_prompt.py.
"""

from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest
from mandate_contracts.ddb import DraftItem, StafferItem

from .test_assistant_api import SID, as_staff, hold_reply, session  # noqa: F401 (fixtures)
from .test_gateway_api import CHIEF, MAYA, _env, client, gateway, store, table  # noqa: F401 (fixtures)

PLATFORM_OPTIONS = [
    {"id": "x", "label": "X"},
    {"id": "bluesky", "label": "Bluesky"},
    {"id": "linkedin", "label": "LinkedIn"},
    {"id": "facebook", "label": "Facebook"},
]


class NeedsConnection(Exception):
    def __init__(self, provider):
        super().__init__(provider)
        self.provider = provider


@pytest.fixture
def conn(monkeypatch):
    """Lane I double: Gmail for email tools, the platform's own account for X, a share link for Bluesky."""
    import identity_gateway.gateway as gw

    d = types.ModuleType("identity_gateway.connections")
    d.NeedsConnection = NeedsConnection
    d.missing = set()
    d.sent = []

    def provider_for_tool(tool, args):
        if tool in ("email_send", "email_send_list"):
            return "gmail"
        if tool in ("social_post", "social_reply") and args.get("platform") == "x":
            return "x"
        return None

    def require_connection(staffer_id, provider):
        if provider in d.missing:
            raise NeedsConnection(provider)

    def send_gmail(staffer_id, to, subject, body, in_reply_to=None):
        d.sent.append((staffer_id, to, subject, body))
        return {"status": "sent", "provider": "gmail"}

    d.provider_for_tool, d.require_connection, d.send_gmail = provider_for_tool, require_connection, send_gmail
    d.needs_connection_result = lambda exc: {
        "status": "needs_connection",
        "provider": exc.provider,
        "label": exc.provider,
        "connectUrl": f"/account?connect={exc.provider}",
        "note": "NOT sent.",
    }
    monkeypatch.setattr(gw, "connections", d, raising=False)
    return d


def put_social(store, draft_id="draft-soc", tool="social_post", who="maya", **args):
    """A pending social draft, written straight to the store (the same row the gateway's hold writes)."""
    base = {"account": "official", "text": "Town hall Tuesday", "platform": "bluesky"}
    if tool == "social_reply":
        base["reply_to"] = "post-1"
    store.put_draft(
        draft_id,
        DraftItem(
            status="pending", sid=SID, agent="agent-1", acting_for=who, office="rep-x", tool=tool,
            category=tool, args={**base, **args}, created_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    return draft_id


def put_list(store, draft_id="draft-list"):
    store.put_draft(
        draft_id,
        DraftItem(
            status="pending", sid=SID, agent="agent-1", acting_for="maya", office="rep-x", tool="email_send_list",
            category="mailing_list", args={"subject": "News", "body": "Hello"},
            created_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    return draft_id


def _as_comms(store):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="communications_director"))


def _listed(client, draft_id):
    return next(d for d in client.get("/api/v1/drafts").json()["drafts"] if d["id"] == draft_id)


def _edit_events(store):
    events, _ = store.events_since(None)
    return [e for e in events if e.reason == "edited by staff"]


# --- GET /drafts: the fields the editable card needs -------------------------------


def test_an_email_draft_lists_version_recipient_and_its_editable_fields(client, session, gateway):
    """Without these fields the card cannot show the recipient or know which inputs to make editable."""
    draft_id = hold_reply(gateway)
    as_staff()
    item = _listed(client, draft_id)
    assert item["version"] == 1
    assert item["recipient"] == "jane.doe@example.com"
    assert item["platform"] is None and item["revising"] is False
    assert "platformOptions" in item and "platforms" not in item
    assert set(item["editable"]) == {"subject", "body", "to"}


def test_a_social_draft_lists_its_platform_and_the_platforms_it_can_move_to(client, session, store):
    """The session shows which platform the post goes to; the staffer can only pick a supported one."""
    draft_id = put_social(store, platform="bluesky")
    as_staff()
    item = _listed(client, draft_id)
    assert item["version"] == 1 and item["recipient"] is None and item["platform"] == "bluesky"
    assert item["platformOptions"] == PLATFORM_OPTIONS and item["revising"] is False
    assert "platforms" not in item
    assert set(item["editable"]) == {"text", "platform"}


def test_a_mailing_list_draft_cannot_edit_its_recipients(client, session, store):
    """The list is read from DynamoDB; letting `to` be editable would let a staffer pick recipients."""
    draft_id = put_list(store)
    as_staff()
    item = _listed(client, draft_id)
    assert set(item["editable"]) == {"subject", "body"} and item["recipient"] is None


# --- PATCH: the happy path ---------------------------------------------------------


def test_patching_an_email_draft_updates_it_bumps_the_version_and_marks_it_edited(client, session, gateway):
    """Without the bump a second editor could silently overwrite the first edit."""
    draft_id = hold_reply(gateway)
    as_staff()
    response = client.patch(
        f"/api/v1/drafts/{draft_id}",
        json={"expectedVersion": 1, "subject": "Re: Pothole (update)", "body": "Fixed.", "to": "jane.doe@example.com"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == draft_id and body["version"] == 2 and body["edited"] is True
    item = _listed(client, draft_id)
    assert (item["version"], item["edited"]) == (2, True)
    assert item["args"]["subject"] == "Re: Pothole (update)" and item["args"]["body"] == "Fixed."
    assert item["args"]["in_reply_to"] == "email-7"  # untouched


def test_patching_writes_an_edited_by_staff_decision_event(client, session, gateway, store):
    """Without the event the audit trail would not show that a human changed the agent's words."""
    draft_id = hold_reply(gateway)
    as_staff()
    client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "New body"})
    events = _edit_events(store)
    assert len(events) == 1
    assert (events[0].acting_for, events[0].tool, events[0].decision) == ("maya", "email_send", "ALLOW")


def test_a_second_patch_with_the_new_version_succeeds(client, session, gateway):
    draft_id = hold_reply(gateway)
    as_staff()
    client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "one"})
    again = client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 2, "body": "two"})
    assert again.status_code == 200 and again.json()["version"] == 3


def test_patching_a_social_drafts_text_and_platform(client, session, store):
    """The session names the platform the staffer wants; both the wording and the platform are theirs to change."""
    draft_id = put_social(store, platform="bluesky")
    as_staff()
    response = client.patch(
        f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "text": "Town hall moved to 7pm", "platform": "linkedin"}
    )
    assert response.status_code == 200, response.text
    item = _listed(client, draft_id)
    assert (item["version"], item["edited"], item["platform"]) == (2, True, "linkedin")
    assert item["args"]["text"] == "Town hall moved to 7pm" and item["args"]["account"] == "official"


def test_a_platform_outside_the_supported_set_is_rejected(client, session, store):
    """Removing the platform allow-list would let the draft target an arbitrary connector name."""
    draft_id = put_social(store)
    as_staff()
    response = client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "platform": "myspace"})
    assert response.status_code == 422
    assert _listed(client, draft_id)["version"] == 1


# --- PATCH: only the editable keys -------------------------------------------------


@pytest.mark.parametrize("key,value", [("account", "personal"), ("tool", "social_post"), ("in_reply_to", "email-1")])
def test_keys_outside_the_drafts_editable_list_are_refused_on_email(client, session, gateway, key, value):
    """Allowing `account`, `tool` or `in_reply_to` would change what Cedar and the category check judged."""
    draft_id = hold_reply(gateway)
    as_staff()
    response = client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, key: value})
    assert response.status_code == 422
    assert _listed(client, draft_id)["version"] == 1


@pytest.mark.parametrize("key,value", [("account", "personal"), ("to", "x@evil.example"), ("subject", "hi")])
def test_keys_outside_the_drafts_editable_list_are_refused_on_social(client, session, store, key, value):
    """A `to` on a social draft, or switching the account, would smuggle in what the tool does not take."""
    draft_id = put_social(store)
    as_staff()
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, key: value}).status_code == 422


def test_to_cannot_be_edited_on_a_mailing_list_draft(client, session, store):
    draft_id = put_list(store)
    as_staff()
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "to": "a@b.example"}).status_code == 422


def test_an_unknown_key_is_a_422_not_ignored(client, session, gateway):
    """Without extra=forbid a typo or an injected field would be silently dropped, hiding a bad client."""
    draft_id = hold_reply(gateway)
    as_staff()
    response = client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "bcc": "x@evil.example"})
    assert response.status_code == 422


def test_expected_version_is_required(client, session, gateway):
    """Without it a blind overwrite is possible."""
    draft_id = hold_reply(gateway)
    as_staff()
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"body": "x"}).status_code == 422


# --- PATCH: ownership and state ----------------------------------------------------


def test_another_staffers_draft_is_a_404(client, session, gateway):
    """Without the owner check anyone logged in could rewrite a colleague's outgoing mail."""
    draft_id = hold_reply(gateway)
    as_staff(CHIEF)
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "x"}).status_code == 404


def test_an_unknown_draft_is_a_404(client, session):
    as_staff()
    assert client.patch("/api/v1/drafts/nope", json={"expectedVersion": 1, "body": "x"}).status_code == 404


def test_a_sent_draft_cannot_be_edited(client, session, gateway, conn):
    """Without the status check an already-sent email could be 'edited', rewriting the record of what went out."""
    draft_id = hold_reply(gateway)
    as_staff()
    assert client.post(f"/api/v1/drafts/{draft_id}/approve", json={}).status_code == 200
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "x"}).status_code == 409


def test_a_rejected_draft_cannot_be_edited(client, session, gateway):
    draft_id = hold_reply(gateway)
    as_staff()
    assert client.post(f"/api/v1/drafts/{draft_id}/reject").status_code == 200
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "x"}).status_code == 409


def test_a_stale_expected_version_is_a_409_with_the_current_draft(client, session, gateway):
    """Without the check, two editors (staffer and agent revision) would overwrite each other unseen."""
    draft_id = hold_reply(gateway)
    as_staff()
    client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "first"})
    response = client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "second"})
    assert response.status_code == 409
    payload = response.json()
    assert payload["error"] == "stale_version"
    assert payload["draft"]["id"] == draft_id and payload["draft"]["version"] == 2
    assert payload["draft"]["args"]["body"] == "first"
    assert _listed(client, draft_id)["args"]["body"] == "first"


# --- approve / reject with expectedVersion -----------------------------------------


def test_approving_with_a_stale_expected_version_sends_nothing(client, session, gateway, conn, store):
    """Approving what the staffer is no longer looking at (the agent revised it) must not send it."""
    draft_id = hold_reply(gateway)
    as_staff()
    client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "edited"})
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"expectedVersion": 1})
    assert response.status_code == 409
    assert conn.sent == []
    assert store.get_draft(draft_id).status == "pending"


def test_approving_with_the_current_expected_version_sends_the_edited_text(client, session, gateway, conn):
    draft_id = hold_reply(gateway)
    as_staff()
    client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "edited"})
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"expectedVersion": 2})
    assert response.status_code == 200 and response.json()["status"] == "sent" and response.json()["edited"] is True
    assert [(who, to, body) for who, to, _subject, body in conn.sent] == [("maya", "jane.doe@example.com", "edited")]


def test_rejecting_with_a_stale_expected_version_is_a_409_and_keeps_the_draft(client, session, gateway, store):
    draft_id = hold_reply(gateway)
    as_staff()
    client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "body": "edited"})
    response = client.post(f"/api/v1/drafts/{draft_id}/reject", json={"expectedVersion": 1})
    assert response.status_code == 409
    assert store.get_draft(draft_id).status == "pending"


# --- escalation: an edit never skips the approval-time check ------------------------


def test_editing_the_recipient_to_an_unknown_address_is_refused_at_patch_time(client, session, gateway, conn, store):
    """The guard is Cedar's recipientKnown check on the new `to` at PATCH time. Without it the card
    would accept any address and only fail later (or never, if approval were skipped)."""
    draft_id = hold_reply(gateway)
    as_staff()
    response = client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "to": "stranger@evil.example"})
    assert response.status_code == 403 and "detail" in response.json()
    assert conn.sent == []
    item = _listed(client, draft_id)
    assert item["version"] == 1 and item["args"]["to"] == "jane.doe@example.com"
    assert _edit_events(store) == []


def test_editing_the_platform_to_an_unconnected_x_is_allowed_then_approval_asks_to_connect(
    client, session, store, conn
):
    """The connection is checked at send time, not at PATCH. Without Lane I's check on the *edited*
    platform at approval, the post would go through an account the staffer never connected."""
    _as_comms(store)
    draft_id = put_social(store, platform="bluesky")
    conn.missing = {"x"}
    as_staff()
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "platform": "x"}).status_code == 200
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"expectedVersion": 2})
    assert response.status_code == 200
    assert response.json() == {"id": draft_id, **conn.needs_connection_result(NeedsConnection("x"))}
    assert store.get_draft(draft_id).status == "pending"


def test_a_caseworker_cannot_edit_a_social_draft_to_another_platform(client, session, store, conn):
    """The role check on the new `platform` runs at PATCH. Without it a role that may not post could
    still reshape a social draft toward a platform it was never allowed to use."""
    draft_id = put_social(store, platform="bluesky")  # maya is a caseworker here
    as_staff()
    response = client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "platform": "linkedin"})
    assert response.status_code == 403 and "detail" in response.json()
    assert store.get_draft(draft_id).args["platform"] == "bluesky"
    assert store.get_draft(draft_id).version == 1


def test_a_caseworker_still_cannot_approve_a_social_draft(client, session, store, conn):
    """The role check at approval (Cedar forbid for non-posting roles). Without it a social draft held
    for a caseworker would publish on approve."""
    draft_id = put_social(store, platform="bluesky")
    as_staff()
    assert client.post(f"/api/v1/drafts/{draft_id}/approve", json={}).status_code == 403
    assert store.get_draft(draft_id).status == "pending"


def test_a_staffer_demoted_after_a_valid_edit_is_denied_at_approval(client, session, store, conn):
    """The edit passed at PATCH, but send_draft re-checks the staffer's role at approve. Without that
    second check a demotion between edit and approval would not stop the post."""
    _as_comms(store)
    draft_id = put_social(store, platform="bluesky")
    as_staff()
    assert client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "platform": "linkedin"}).status_code == 200
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role="caseworker"))
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"expectedVersion": 2})
    assert response.status_code == 403
    assert store.get_draft(draft_id).status == "pending"


# --- POST /drafts/{id}/revise: ask the assistant to rewrite it ---------------------


def _messages(client):
    return client.get(f"/api/v1/sessions/{SID}/messages").json()["messages"]


def test_revise_returns_202_marks_the_draft_revising_and_posts_the_instruction_in_its_session(
    client, session, gateway
):
    """Without the chat post the assistant never hears the request; without `revising` the card cannot
    show it is waiting."""
    draft_id = hold_reply(gateway)
    as_staff()
    response = client.post(f"/api/v1/drafts/{draft_id}/revise", json={"instruction": "make it shorter"})
    assert response.status_code == 202
    assert response.json() == {"id": draft_id, "revising": True}
    assert _listed(client, draft_id)["revising"] is True
    posted = [m for m in _messages(client) if m["role"] == "user"]
    assert len(posted) == 1 and "make it shorter" in posted[0]["text"] and draft_id in posted[0]["text"]


def test_revise_twice_is_a_409(client, session, gateway):
    """Without the check two instructions race on one draft."""
    draft_id = hold_reply(gateway)
    as_staff()
    client.post(f"/api/v1/drafts/{draft_id}/revise", json={"instruction": "shorter"})
    assert client.post(f"/api/v1/drafts/{draft_id}/revise", json={"instruction": "warmer"}).status_code == 409
    assert len([m for m in _messages(client) if m["role"] == "user"]) == 1


@pytest.mark.parametrize("decide", ["approve", "reject"])
def test_revise_on_a_decided_draft_is_a_409(client, session, gateway, conn, decide):
    draft_id = hold_reply(gateway)
    as_staff()
    assert client.post(f"/api/v1/drafts/{draft_id}/{decide}", json={}).status_code == 200
    assert client.post(f"/api/v1/drafts/{draft_id}/revise", json={"instruction": "shorter"}).status_code == 409


def test_revise_on_another_staffers_draft_is_a_404_and_posts_nothing(client, session, gateway):
    """Without the owner check anyone could talk to a colleague's assistant."""
    draft_id = hold_reply(gateway)
    as_staff(CHIEF)
    assert client.post(f"/api/v1/drafts/{draft_id}/revise", json={"instruction": "shorter"}).status_code == 404
    as_staff()
    assert [m for m in _messages(client) if m["role"] == "user"] == []


@pytest.mark.parametrize("instruction", ["", "x" * 501])
def test_revise_instruction_must_be_1_to_500_chars(client, session, gateway, instruction):
    draft_id = hold_reply(gateway)
    as_staff()
    assert client.post(f"/api/v1/drafts/{draft_id}/revise", json={"instruction": instruction}).status_code == 422


def test_revise_accepts_exactly_500_chars(client, session, gateway):
    draft_id = hold_reply(gateway)
    as_staff()
    assert client.post(f"/api/v1/drafts/{draft_id}/revise", json={"instruction": "x" * 500}).status_code == 202


def test_a_communications_director_can_post_the_edited_social_draft(client, session, store, conn):
    """Control for the two tests above: the same edit succeeds for a role that may post."""
    _as_comms(store)
    draft_id = put_social(store, platform="bluesky")
    as_staff()
    client.patch(f"/api/v1/drafts/{draft_id}", json={"expectedVersion": 1, "text": "Moved to 7pm", "platform": "linkedin"})
    response = client.post(f"/api/v1/drafts/{draft_id}/approve", json={"expectedVersion": 2})
    assert response.status_code == 200 and response.json()["status"] == "sent" and response.json()["edited"] is True
