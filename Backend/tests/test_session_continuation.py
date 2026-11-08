"""After the 1-hour session ends, the conversation carries on in a fresh session instead of vanishing.

Huy: "a new prompt deletes my old prompts". The old prompts were still stored, under the expired sid; the
console just showed the new sid. `POST /sessions {"continues": <old sid>}` links the two. Each link is still a
fresh 1-hour session with fresh mandates, and one-time grants do not carry over. moto DynamoDB, no AWS.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from .test_conversations import DANA, hold_draft, listed, login, open_conversation
from .test_gateway_api import _env, as_admin, client, gateway, store, table  # noqa: F401 (fixtures)


def expire(store, sid):
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.put_session(sid, store.get_session(sid).model_copy(update={"created_at": old}))


def continue_(client, old, who=None):
    login(who) if who else login()
    return client.post("/api/v1/sessions", json={"continues": old})


def say(client, sid, text):
    login()
    assert client.post(f"/api/v1/sessions/{sid}/messages", json={"text": text}).status_code == 200


# --- the guard ---------------------------------------------------------------------------------


def test_an_expired_conversation_continues_in_a_fresh_session(client, store):
    old = open_conversation(client, "first question")
    expire(store, old)

    r = continue_(client, old)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sid"] != old and body["continuesFrom"] == old
    fresh = datetime.fromisoformat(body["expiresAt"]) - datetime.now(timezone.utc)
    assert timedelta(minutes=59) < fresh <= timedelta(hours=1), "a fresh one-hour session"
    assert store.get_session(body["sid"]).status == "active"


def test_a_session_still_running_cannot_be_continued(client):
    old = open_conversation(client, "first question")
    r = continue_(client, old)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "still_active"


def test_a_revoked_session_can_never_be_continued(client, store):
    old = open_conversation(client, "first question")
    expire(store, old)
    store.put_session(old, store.get_session(old).model_copy(update={"status": "revoked"}))
    assert continue_(client, old).status_code == 403


def test_an_offboarded_staffer_cannot_continue(client, store):
    old = open_conversation(client, "first question")
    expire(store, old)
    as_admin()
    client.post("/api/v1/staff/maya/offboard")
    assert continue_(client, old).status_code == 403


def test_nobody_can_continue_someone_elses_conversation(client, store):
    mayas = open_conversation(client, "private matter")
    expire(store, mayas)
    r = continue_(client, mayas, who=DANA)
    assert r.status_code == 404
    assert listed(client, DANA) == [], "nothing was created for Dana either"


def test_an_unknown_or_deleted_conversation_cannot_be_continued(client, store):
    assert continue_(client, "no-such-sid").status_code == 404
    old = open_conversation(client, "to be deleted")
    expire(store, old)
    login()
    client.delete(f"/api/v1/sessions/{old}")
    assert continue_(client, old).status_code == 404


def test_only_the_newest_link_continues_so_two_tabs_cannot_fork_it(client, store):
    old = open_conversation(client, "first question")
    expire(store, old)
    assert continue_(client, old).status_code == 200
    r = continue_(client, old)  # a second tab, still on the old sid
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_continued"


def test_a_chain_whose_newest_link_is_still_active_cannot_be_continued(client, store):
    old = open_conversation(client, "first question")
    expire(store, old)
    newest = continue_(client, old).json()["sid"]
    assert continue_(client, newest).status_code == 409


def test_an_unknown_field_is_refused(client):
    login()
    assert client.post("/api/v1/sessions", json={"continues": "x", "actingFor": "dana"}).status_code == 422


def test_starting_a_new_conversation_still_works_without_continues(client):
    login()
    assert client.post("/api/v1/sessions", json={}).status_code == 200


# --- least privilege: one-time grants stay with their session ------------------------------------


def test_one_time_grants_do_not_carry_over(client, store):
    old = open_conversation(client, "first question")
    store.put_grant(old, "case_status", "maya", datetime.now(timezone.utc).isoformat(), 2_000_000_000)
    expire(store, old)

    body = continue_(client, old).json()

    assert body["grantsReset"] is True
    assert store.grants_for(body["sid"]) == []


def test_no_reset_notice_when_there_was_nothing_to_reset(client, store):
    old = open_conversation(client, "first question")
    expire(store, old)
    assert continue_(client, old).json()["grantsReset"] is False


# --- one conversation in the sidebar, the whole history readable ----------------------------------


def test_the_sidebar_shows_one_conversation_per_chain(client, store):
    old = open_conversation(client, "Reply to the pothole complaints")
    login()
    client.patch(f"/api/v1/sessions/{old}", json={"title": "Pothole replies", "pinned": True})
    hold_draft(store, old, 1)
    expire(store, old)
    middle = continue_(client, old).json()["sid"]
    expire(store, middle)
    newest = continue_(client, middle).json()["sid"]
    hold_draft(store, newest, 2)
    open_conversation(client, "something unrelated")

    rows = listed(client)
    chain = [r for r in rows if r["sid"] == newest]
    assert len(rows) == 2 and len(chain) == 1
    (row,) = chain
    assert row["earlier"] == [middle, old]
    assert (row["title"], row["pinned"], row["status"]) == ("Pothole replies", True, "active")
    assert row["waitingDrafts"] == 2, "drafts from every link"


def test_a_rename_or_pin_on_any_link_applies_to_the_conversation(client, store):
    old = open_conversation(client, "hello")
    expire(store, old)
    newest = continue_(client, old).json()["sid"]
    login()
    client.patch(f"/api/v1/sessions/{newest}", json={"title": "Budget notes"})
    assert next(r for r in listed(client) if r["sid"] == newest)["title"] == "Budget notes"


def test_earlier_messages_stay_readable_by_the_owner_only(client, store):
    old = open_conversation(client, "my first prompt")
    expire(store, old)
    newest = continue_(client, old).json()["sid"]
    say(client, newest, "my second prompt")

    login()
    assert [m["text"] for m in client.get(f"/api/v1/sessions/{old}/messages").json()["messages"]] == ["my first prompt"]
    login(DANA)
    assert client.get(f"/api/v1/sessions/{old}/messages").status_code == 404


def test_deleting_the_conversation_deletes_every_link(client, store):
    old = open_conversation(client, "hello")
    expire(store, old)
    newest = continue_(client, old).json()["sid"]
    login()
    assert client.delete(f"/api/v1/sessions/{newest}").status_code == 200
    assert listed(client) == []
    login()
    assert client.get(f"/api/v1/sessions/{old}/messages").status_code == 404
    assert store.get_session(newest).status == "revoked"


def test_search_covers_the_whole_conversation(client, store):
    old = open_conversation(client, "the zebra crossing on Elm")
    expire(store, old)
    newest = continue_(client, old).json()["sid"]
    login()
    found = client.get("/api/v1/sessions", params={"q": "zebra"}).json()["sessions"]
    assert [r["sid"] for r in found] == [newest]


@pytest.mark.parametrize("hops", [3])
def test_a_long_chain_stays_one_conversation(client, store, hops):
    sid = open_conversation(client, "start")
    for _ in range(hops):
        expire(store, sid)
        sid = continue_(client, sid).json()["sid"]
    rows = listed(client)
    assert len(rows) == 1 and rows[0]["sid"] == sid and len(rows[0]["earlier"]) == hops


def test_a_continued_session_does_not_ask_for_a_new_title(client, store):
    from app.services import conversations

    calls = []
    conversations.set_title_model(lambda messages: calls.append(messages) or "Reply to pothole complaints")
    try:
        old = open_conversation(client, "first question")
        expire(store, old)
        newest = continue_(client, old).json()["sid"]
        say(client, newest, "second question")
    finally:
        conversations.set_title_model(None)
    assert calls == [["first question"]]
    assert next(r for r in listed(client) if r["sid"] == newest)["title"] == "Reply to pothole complaints"


def test_the_claim_lets_only_one_tab_continue(client, store):
    from app.services import conversations

    old = open_conversation(client, "first question")
    assert conversations.claim_continuation(old, "tab-1", "maya") is True
    assert conversations.claim_continuation(old, "tab-2", "maya") is False  # the race the pre-check can't see


def test_the_pre_check_refuses_a_continued_session_on_its_own(client, store, monkeypatch):
    from app.services import conversations

    old = open_conversation(client, "first question")
    expire(store, old)
    assert continue_(client, old).status_code == 200
    monkeypatch.setattr(conversations, "claim_continuation", lambda *args: True)  # as if the claim raced past
    assert continue_(client, old).json()["detail"]["code"] == "already_continued"


def test_deleting_revokes_every_link_still_marked_active(client, store):
    old = open_conversation(client, "hello")
    expire(store, old)
    newest = continue_(client, old).json()["sid"]
    login()
    client.delete(f"/api/v1/sessions/{newest}")
    assert store.get_session(old).status == "revoked"


def test_deleting_through_the_first_session_hides_the_later_ones(client, store):
    old = open_conversation(client, "hello")
    expire(store, old)
    newest = continue_(client, old).json()["sid"]
    login()
    assert client.delete(f"/api/v1/sessions/{old}").status_code == 200
    assert client.get(f"/api/v1/sessions/{newest}/messages").status_code == 404
    assert client.post(f"/api/v1/sessions/{newest}/messages", json={"text": "hi"}).status_code in (403, 404)


def test_a_tab_that_loses_the_race_after_the_pre_check_gets_409_not_an_orphan_session(client, store, monkeypatch):
    """The pre-check saw nothing, but another tab claimed the old session a moment later: refuse, create nothing."""
    from app.services import conversations

    old = open_conversation(client, "first question")
    expire(store, old)
    monkeypatch.setattr(conversations, "claim_continuation", lambda *args: False)
    before = {sid for sid, _ in store.sessions_for("maya")}

    r = continue_(client, old)

    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_continued"
    assert {sid for sid, _ in store.sessions_for("maya")} == before, "no orphan session for the losing tab"
