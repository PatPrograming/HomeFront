"""Least privilege at the API: nothing a staffer or a client sends can give the agent more than the
staffer's own office role (docs/ROLES.md, "Least privilege: what we guarantee")."""

from __future__ import annotations

import pytest
from mandate_contracts.ddb import StafferItem

from app.main import app

from .test_assistant_api import SID, _send, agent_headers, as_staff, session  # noqa: F401 (fixtures)
from .test_gateway_api import CHIEF, MAYA, _env, client, gateway, store, table  # noqa: F401 (fixtures)


def _as_role(store, office_role):
    store.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer", office_role=office_role))


# ---- grants and "auto" cannot exceed the role ------------------------------------


@pytest.mark.parametrize(
    "office_role,category,expected",
    [
        ("caseworker", "case_status", 200),  # caseworkers email constituents
        ("caseworker", "social_post", 403),  # but do not post
        ("communications_director", "social_post", 200),
        ("caseworker", "social_reply", 403),  # replies are a social_reply tool, not email
        ("legislative_aide", "social_reply", 403),
        ("communications_director", "social_reply", 200),
        ("chief_of_staff", "social_reply", 200),
        ("legislative_aide", "case_status", 403),  # no email at all
        ("intern", "info_request", 403),
        (None, "case_status", 403),  # no role on file
    ],
)
def test_a_session_grant_outside_the_role_is_refused(client, session, store, office_role, category, expected):
    _as_role(store, office_role)
    as_staff()
    r = client.post(f"/api/v1/sessions/{SID}/grants", json={"category": category})
    assert r.status_code == expected, r.text
    assert (category in store.grants_for(SID)) is (expected == 200)
    if expected == 403:
        assert "Not part of your role" in r.json()["detail"]


@pytest.mark.parametrize(
    "office_role,category,expected",
    [
        ("caseworker", "case_status", 200),
        ("caseworker", "social_post", 403),
        ("intern", "scheduling", 403),
        ("caseworker", "social_reply", 403),
        ("communications_director", "social_reply", 200),
    ],
)
def test_auto_outside_the_role_is_refused(client, session, store, office_role, category, expected):
    _as_role(store, office_role)
    as_staff()
    r = client.put(f"/api/v1/autonomy/{category}", json={"level": "auto"})
    assert r.status_code == expected, r.text
    assert (store.get_autonomy("maya", category).level == "auto") is (expected == 200)
    # Turning a category back to "review" is always allowed: it only takes power away.
    assert client.put(f"/api/v1/autonomy/{category}", json={"level": "review"}).status_code == 200


# ---- the mailing list is locked: never alone, for any role ---------------------------


@pytest.mark.parametrize("office_role", ["chief_of_staff", "caseworker", "communications_director"])
def test_mailing_list_is_locked_so_no_grant_and_no_auto_even_for_the_chief(client, session, store, office_role):
    """Removing the LOCKED entry would let a session grant or "auto" send to every constituent unreviewed."""
    from identity_gateway.autonomy import LOCKED

    assert "mailing_list" in LOCKED
    _as_role(store, office_role)
    as_staff()
    r = client.post(f"/api/v1/sessions/{SID}/grants", json={"category": "mailing_list"})
    assert r.status_code == 403 and "always needs your review" in r.json()["detail"]
    assert "mailing_list" not in store.grants_for(SID)
    r = client.put("/api/v1/autonomy/mailing_list", json={"level": "auto"})
    assert r.status_code == 403 and "always needs your review" in r.json()["detail"]
    assert store.get_autonomy("maya", "mailing_list").level != "auto"


def test_the_new_autonomy_categories_are_known_and_social_reply_is_not_locked(client, session, store):
    from identity_gateway.autonomy import CATEGORIES, CATEGORY_TOOL, LOCKED

    assert {"mailing_list", "social_reply"} <= set(CATEGORIES)
    assert "social_reply" not in LOCKED
    assert CATEGORY_TOOL["mailing_list"] == "email_send_list"
    assert CATEGORY_TOOL["social_reply"] == "social_reply"
    _as_role(store, "communications_director")
    as_staff()
    rows = {row["category"]: row for row in client.get("/api/v1/autonomy").json()["categories"]}
    assert rows["mailing_list"]["locked"] is True and rows["social_reply"]["locked"] is False


def test_an_approved_draft_of_each_new_outbound_tool_has_editable_fields():
    """A missing entry raises KeyError when a held draft is approved."""
    from app.routers.assistant import EDITABLE_FIELDS

    assert EDITABLE_FIELDS["social_reply"] == ("text",)
    assert EDITABLE_FIELDS["email_send_list"] == ("subject", "body")


# ---- role, office and category sent by a client are ignored or rejected ----------


SNEAKY = {"role": "chief_of_staff", "officeRole": "chief_of_staff", "office": "rep-y", "actingFor": "chiefofstaff"}


def _unchanged(store):
    staffer = store.get_staffer("maya")
    session = store.get_session(SID)
    return (staffer.office_role, staffer.office, session.office, session.acting_for)


def test_client_sent_role_or_office_changes_nothing_on_any_route(client, session, store, gateway):
    before = _unchanged(store)
    as_staff()
    # Fields these routes do not define: rejected (422) where the model forbids extras, otherwise dropped.
    assert client.post(f"/api/v1/sessions/{SID}/messages", json={"text": "hi", **SNEAKY}).status_code == 200
    assert client.post(f"/api/v1/sessions/{SID}/grants", json={"category": "case_status", **SNEAKY}).status_code == 422
    assert client.put("/api/v1/autonomy/case_status", json={"level": "review", **SNEAKY}).status_code == 200
    assert client.post("/api/v1/agent/messages", json={"text": "hi", **SNEAKY}, headers=agent_headers()).status_code == 200
    from .test_assistant_api import hold_reply

    draft_id = hold_reply(gateway)
    assert client.post(f"/api/v1/drafts/{draft_id}/approve", json={"office": "rep-y"}).status_code == 422
    assert _unchanged(store) == before
    # The new session's role still comes from the STAFFER item.
    from app.routers import sessions

    app.dependency_overrides[sessions.require_staff] = lambda: MAYA
    body = client.post("/api/v1/sessions", json=SNEAKY).json()
    assert body["role"] == "caseworker" and body["office"] == "rep-x" and body["actingFor"] == "maya"


def test_a_category_cannot_be_chosen_by_the_agent(client, session, gateway, store):
    """The category of an outbound reply comes from the inbox email in DynamoDB, never from the call."""
    as_staff()
    assert client.post(f"/api/v1/sessions/{SID}/grants", json={"category": "case_status"}).status_code == 200
    token = agent_headers()["Authorization"].removeprefix("Bearer ")
    # A reply that names no inbox email is a new conversation (locked), whatever category it claims.
    args = {"to": "jane.doe@example.com", "subject": "s", "body": "b", "category": "case_status"}
    assert gateway.guarded("email_send", args, token).result["status"] == "held_for_review"


def test_grants_are_per_session_and_die_with_offboarding(client, session, gateway, store, office_ses):
    as_staff()
    client.post(f"/api/v1/sessions/{SID}/grants", json={"category": "case_status"})
    assert _send(gateway).event.reason == "allowed"  # the grant works on its own session
    store.put_session("sid-second", store.get_session(SID))  # a second console task, same staffer
    assert _send(gateway, "sid-second").result["status"] == "held_for_review"  # not inherited
    store.set_staffer_active("maya", False)
    store.revoke_sessions_for("maya")
    assert store.grants_for(SID) == []
