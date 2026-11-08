"""Several conversations at once: each one is its own session (sid, mandate, grants, drafts, chat).

moto DynamoDB and a fake agent launcher: no real process, no AWS, no Bedrock.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from mandate_contracts.ddb import DraftItem

from app.main import app
from app.routers import assistant, events, sessions
from app.routers.sessions import StaffIdentity
from app.services import agent_supervisor
from app.services.agent_supervisor import AgentSupervisor

from .test_agent_autostart import FakeLauncher
from .test_gateway_api import MAYA, _env, as_admin, client, gateway, store, table  # noqa: F401 (fixtures)

DANA = StaffIdentity("dana", "sub-dana", None, frozenset({"staffer"}))


def login(identity=MAYA):
    for router in (sessions, events, assistant):
        app.dependency_overrides[router.require_staff] = lambda: identity


def open_conversation(client, first: str | None = None, who=MAYA) -> str:
    login(who)
    r = client.post("/api/v1/sessions", json={})
    assert r.status_code == 200, r.text
    sid = r.json()["sid"]
    if first is not None:
        assert client.post(f"/api/v1/sessions/{sid}/messages", json={"text": first}).status_code == 200
    return sid


def listed(client, who=MAYA) -> list[dict]:
    login(who)
    r = client.get("/api/v1/sessions")
    assert r.status_code == 200, r.text
    return r.json()["sessions"]


def hold_draft(store, sid: str, n: int, who="maya") -> None:
    store.put_draft(f"draft-{sid}-{n}", DraftItem(
        status="pending", sid=sid, agent="agent-1", acting_for=who, office="rep-x", tool="email_send",
        category="case_status", args={"to": "jane.doe@example.com", "subject": "Re", "body": "Hi"},
        created_at=datetime.now(timezone.utc).isoformat(),
    ))


@pytest.fixture
def launcher():
    fake = FakeLauncher()
    agent_supervisor.set_supervisor(AgentSupervisor(fake, max_starts=3, max_running_per_staffer=3))
    yield fake
    agent_supervisor.set_supervisor(None)


# --- listing ------------------------------------------------------------------------


def test_a_staffer_lists_each_of_their_open_conversations(client, store):
    first = open_conversation(client, "Check my email please")
    second = open_conversation(client, "Post about the town hall")
    hold_draft(store, first, 1)
    hold_draft(store, first, 2)
    hold_draft(store, second, 1)

    rows = {row["sid"]: row for row in listed(client)}

    assert set(rows) == {first, second}
    assert rows[first]["title"] == "Check my email please"
    assert rows[first]["waitingDrafts"] == 2 and rows[second]["waitingDrafts"] == 1
    for row in rows.values():
        assert set(row) == {"sid", "title", "createdAt", "expiresAt", "status", "waitingDrafts"}
        assert row["status"] == "active"


def test_the_newest_conversation_comes_first(client):
    opened = [open_conversation(client, f"conversation {n}") for n in range(4)]  # within the same second
    assert [row["sid"] for row in listed(client)] == opened[::-1]


def test_the_title_is_the_first_message_trimmed(client):
    sid = open_conversation(client, "   " + "Find the FEMA flood claims for Elm St " * 5 + "  ")
    login()
    client.post(f"/api/v1/sessions/{sid}/messages", json={"text": "a later message"})
    title = next(row["title"] for row in listed(client) if row["sid"] == sid)
    assert title.startswith("Find the FEMA flood claims") and len(title) <= 60 and title == title.strip()


@pytest.mark.parametrize("first", [None, "   ", "\n\t"])
def test_a_conversation_with_no_words_yet_has_a_placeholder_title(client, first):
    sid = open_conversation(client, first)
    assert next(row["title"] for row in listed(client) if row["sid"] == sid) == "New conversation"


def test_a_deactivated_staffers_conversations_are_listed_as_revoked(client, store):
    sid = open_conversation(client, "before deactivation")
    store.set_staffer_active("maya", False)  # deactivated without the sessions being revoked yet
    assert next(row["status"] for row in listed(client) if row["sid"] == sid) == "revoked"


def test_an_expired_conversation_is_listed_as_expired(client, store):
    sid = open_conversation(client, "old one")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.put_session(sid, store.get_session(sid).model_copy(update={"created_at": old}))
    assert next(row["status"] for row in listed(client) if row["sid"] == sid) == "expired"


def test_each_conversation_has_its_own_chat_and_drafts(client, store):
    first = open_conversation(client, "only in the first")
    second = open_conversation(client, "only in the second")
    hold_draft(store, first, 1)

    login()
    texts = [m["text"] for m in client.get(f"/api/v1/sessions/{second}/messages").json()["messages"]]
    assert texts == ["only in the second"]
    drafts = client.get("/api/v1/drafts", params={"sid": second}).json()["drafts"]
    assert drafts == []
    assert [d["sid"] for d in client.get("/api/v1/drafts", params={"sid": first}).json()["drafts"]] == [first]
    assert len(client.get("/api/v1/drafts").json()["drafts"]) == 1  # no filter: every waiting draft, as before


# --- isolation between staffers -----------------------------------------------------------


def test_another_staffer_never_sees_or_reads_your_conversations(client, store):
    mine = open_conversation(client, "private constituent matter")
    hold_draft(store, mine, 1)

    assert listed(client, DANA) == []
    login(DANA)
    assert client.get(f"/api/v1/sessions/{mine}").status_code == 404
    assert client.get(f"/api/v1/sessions/{mine}/messages").status_code == 404
    assert client.post(f"/api/v1/sessions/{mine}/messages", json={"text": "hi"}).status_code == 404
    assert client.get("/api/v1/drafts", params={"sid": mine}).json()["drafts"] == []


def test_listing_needs_a_login(client):
    app.dependency_overrides.clear()
    assert client.get("/api/v1/sessions").status_code == 401


# --- offboarding --------------------------------------------------------------------------


def test_offboarding_revokes_and_stops_every_open_conversation(client, gateway, launcher):
    first = open_conversation(client, "one")
    second = open_conversation(client, "two")
    assert len(launcher.started) == 2

    as_admin()
    r = client.post("/api/v1/staff/maya/offboard")
    assert r.status_code == 200 and r.json()["revokedSessions"] == 2

    assert all(proc.terminated for _, proc in launcher.started)
    assert {row["sid"]: row["status"] for row in listed(client)} == {first: "revoked", second: "revoked"}
    login()
    assert client.post(f"/api/v1/sessions/{second}/messages", json={"text": "still there?"}).status_code == 403


# --- agents: one per conversation, a cap per staffer --------------------------------------


def test_each_conversation_gets_its_own_agent(client, launcher):
    first = open_conversation(client, "one")
    second = open_conversation(client, "two")
    login()
    client.post(f"/api/v1/sessions/{first}/messages", json={"text": "one again"})
    assert sorted(sid for sid, _ in launcher.started) == sorted([first, second])


def test_a_fourth_running_conversation_is_refused_clearly(client, store, launcher):
    for n in range(3):
        open_conversation(client, f"conversation {n}")
    fourth = open_conversation(client)
    login()

    r = client.post(f"/api/v1/sessions/{fourth}/messages", json={"text": "and one more"})

    assert r.status_code == 503 and "too many conversations running" in r.json()["detail"]
    assert len(launcher.started) == 3
    assert store.chat_since(fourth, None)[0] == [], "a refused message is not stored"


def test_the_cap_counts_running_agents_only(client, launcher):
    for n in range(3):
        open_conversation(client, f"conversation {n}")
    launcher.started[0][1].crash()  # one agent ended: room for another
    open_conversation(client, "a fourth fits now")
    assert len(launcher.started) == 4


def test_the_cap_is_per_staffer(client, store, launcher):
    for n in range(3):
        open_conversation(client, f"maya {n}")
    danas = open_conversation(client, "dana's first", who=DANA)
    assert len(launcher.started) == 4 and launcher.started[-1][0] == danas


def test_two_tabs_on_one_conversation_start_one_agent(client, store):
    slow = FakeLauncher()
    barrier = threading.Barrier(2)

    def launch(sid):
        time.sleep(0.2)  # both requests are inside ensure_running before either launch returns
        return slow(sid)

    agent_supervisor.set_supervisor(AgentSupervisor(launch))
    try:
        sid = open_conversation(client)
        login()

        def tab(text):
            barrier.wait()
            return client.post(f"/api/v1/sessions/{sid}/messages", json={"text": text}).status_code

        with ThreadPoolExecutor(2) as pool:
            codes = list(pool.map(tab, ["from tab 1", "from tab 2"]))
    finally:
        agent_supervisor.set_supervisor(None)

    assert codes == [200, 200]
    assert len(slow.started) == 1
    assert sorted(m.text for m in store.chat_since(sid, None)[0]) == ["from tab 1", "from tab 2"]
