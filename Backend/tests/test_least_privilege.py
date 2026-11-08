"""Least privilege at the API: nothing a staffer or a client sends can give the agent more than the
staffer's own office role (docs/ROLES.md, "Least privilege: what we guarantee")."""

from __future__ import annotations

import pytest
from mandate_contracts.ddb import StafferItem

from app.main import app

from .test_assistant_api import SID, agent_headers, as_staff, session  # noqa: F401 (fixtures)
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
    [("caseworker", "case_status", 200), ("caseworker", "social_post", 403), ("intern", "scheduling", 403)],
)
def test_auto_outside_the_role_is_refused(client, session, store, office_role, category, expected):
    _as_role(store, office_role)
    as_staff()
    r = client.put(f"/api/v1/autonomy/{category}", json={"level": "auto"})
    assert r.status_code == expected, r.text
    assert (store.get_autonomy("maya", category).level == "auto") is (expected == 200)
    # Turning a category back to "review" is always allowed: it only takes power away.
    assert client.put(f"/api/v1/autonomy/{category}", json={"level": "review"}).status_code == 200


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


def test_grants_are_per_session_and_die_with_offboarding(client, session, gateway, store):
    as_staff()
    client.post(f"/api/v1/sessions/{SID}/grants", json={"category": "case_status"})
    store.put_session("sid-second", store.get_session(SID))  # a second console task, same staffer
    assert store.grants_for("sid-second") == []
    store.set_staffer_active("maya", False)
    store.revoke_sessions_for("maya")
    assert store.grants_for(SID) == []
