"""Connecting a Bluesky account with an app password and posting after approval.
Bluesky is faked at bluesky_api._request, so no test reaches the network."""
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.test_approvals import _agent, _call, _token

from identity_gateway import app as gateway  # noqa: E402
from mcp_tools import bluesky_api  # noqa: E402

client = TestClient(app, follow_redirects=False)
APP_PW = "abcd-efgh-ijkl-mnop"
DID = "did:plc:cityofexample123"


def _jwt(exp):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'none'})}.{enc({'exp': exp})}.sig"


class FakeBsky:
    def __init__(self):
        self.calls = []
        self.sessions = 0
        self.reject_login = False
        self.fail_refresh = None
        self.access_ttl = 7200

    def __call__(self, url, *, method="GET", payload=None, bearer=None):
        self.calls.append({"url": url, "method": method, "payload": payload, "bearer": bearer})
        name = url.rsplit("/", 1)[-1]
        if name == "com.atproto.server.createSession":
            if self.reject_login:
                raise bluesky_api.BlueskyError("HTTP 401: AuthenticationRequired", 401)
            return self._session()
        if name == "com.atproto.server.refreshSession":
            if self.fail_refresh:
                raise bluesky_api.BlueskyError(self.fail_refresh, 400)
            return self._session()
        if name == "com.atproto.repo.createRecord":
            return {"uri": f"at://{DID}/app.bsky.feed.post/3kabc", "cid": "bafy"}
        if name == "com.atproto.server.deleteSession":
            return {}
        raise AssertionError(f"unexpected Bluesky call: {url}")

    def _session(self):
        self.sessions += 1
        return {
            "accessJwt": _jwt(int(time.time()) + self.access_ttl),
            "refreshJwt": f"refresh-{self.sessions}",
            "handle": "city.bsky.social",
            "did": DID,
            "didDoc": {"service": [{"id": "#atproto_pds", "serviceEndpoint": "https://pds.example.com"}]},
        }

    def records(self):
        return [c for c in self.calls if c["url"].endswith("createRecord")]


@pytest.fixture
def fake_bsky(monkeypatch):
    fake = FakeBsky()
    monkeypatch.setattr(bluesky_api, "_request", fake)
    return fake


def _connect(agent_id, identifier="@City.bsky.social", password=APP_PW):
    return client.post(
        f"/api/v1/agents/{agent_id}/social/bluesky/connect", json={"identifier": identifier, "app_password": password}
    )


def _agent_connected(fake):
    agent = _agent(kind="social")
    assert _connect(agent["agent_id"]).status_code == 200
    return agent


def test_connect_keeps_the_session_but_never_the_password_or_tokens(fake_bsky):
    agent = _agent(kind="social")
    r = _connect(agent["agent_id"])
    assert r.status_code == 200, r.text
    assert r.json()["social_connections"]["bluesky"]["handle"] == "city.bsky.social"
    [login] = fake_bsky.calls
    assert login["payload"] == {"identifier": "city.bsky.social", "password": APP_PW}  # @ stripped, lower-cased

    stored = gateway.store.get_agent(agent["agent_id"]).social_connections["bluesky"]
    assert APP_PW not in str(stored)  # the password was used once and dropped
    assert stored["service"] == "https://pds.example.com" and stored["did"] == DID
    visible = r.text + client.get(f"/api/v1/agents/{agent['agent_id']}/social").text
    assert stored["access_token"] not in visible and "refresh-1" not in visible


@pytest.mark.parametrize("password", ["my-real-account-password", "abcdefghijklmnop", "abcd-efgh-ijkl", ""])
def test_anything_but_an_app_password_is_refused_before_contacting_bluesky(fake_bsky, password):
    agent = _agent(kind="social")
    r = _connect(agent["agent_id"], password=password)
    assert r.status_code in (400, 422)
    if r.status_code == 400:
        assert r.json()["detail"]["error"] == "not_an_app_password"
    assert fake_bsky.calls == []


def test_wrong_credentials_connect_nothing(fake_bsky):
    fake_bsky.reject_login = True
    agent = _agent(kind="social")
    r = _connect(agent["agent_id"])
    assert r.status_code == 400 and r.json()["detail"]["error"] == "bluesky_login_failed"
    assert client.get(f"/api/v1/agents/{agent['agent_id']}").json()["social_connections"] == {}


def test_publish_then_approve_posts_with_clickable_links(fake_bsky):
    agent = _agent_connected(fake_bsky)
    r = client.post(
        f"/api/v1/agents/{agent['agent_id']}/social/publish",
        json={"platforms": ["bluesky"], "message": "Town hall Tuesday 6pm.", "links": ["https://city.gov/hall"]},
    )
    assert r.status_code == 202, r.text
    [queued] = r.json()["queued"]
    assert fake_bsky.records() == []  # nothing goes out before approval

    pending = client.get(f"/api/v1/approvals/{queued['approval_id']}").json()
    assert pending["social"] == {"platform": "bluesky", "account": "@city.bsky.social", "simulated": False}

    done = client.post(f"/api/v1/approvals/{queued['approval_id']}/approve", json={})
    assert done.status_code == 200, done.text
    result = done.json()["result"]
    assert result["simulated"] is False and result["account"] == "@city.bsky.social"
    assert result["url"] == "https://bsky.app/profile/city.bsky.social/post/3kabc"

    [rec] = fake_bsky.records()
    assert rec["url"].startswith("https://pds.example.com/")  # the account's own server
    assert rec["payload"]["repo"] == DID and rec["payload"]["collection"] == "app.bsky.feed.post"
    record = rec["payload"]["record"]
    [facet] = record["facets"]
    raw = record["text"].encode()
    assert raw[facet["index"]["byteStart"] : facet["index"]["byteEnd"]] == b"https://city.gov/hall"
    assert facet["features"][0]["uri"] == "https://city.gov/hall"
    assert "access" not in done.text.lower().replace("accessible", "")


def test_link_facets_use_utf8_byte_offsets_and_skip_trailing_punctuation():
    text = "Café ☕ update: https://city.gov/a, and (https://city.gov/b)."
    facets = bluesky_api.link_facets(text)
    raw = text.encode()
    got = [raw[f["index"]["byteStart"] : f["index"]["byteEnd"]].decode() for f in facets]
    assert got == ["https://city.gov/a", "https://city.gov/b"]


def test_publish_rejects_posts_over_300_characters(fake_bsky):
    agent = _agent_connected(fake_bsky)
    base = f"/api/v1/agents/{agent['agent_id']}/social/publish"
    r = client.post(base, json={"platforms": ["bluesky"], "message": "a" * 301})
    assert r.json()["queued"] == [] and r.json()["skipped"][0]["reason"] == "too_long"
    # 281-300 is too long for X but fine for Bluesky.
    r = client.post(base, json={"platforms": ["bluesky"], "message": "a" * 290})
    assert len(r.json()["queued"]) == 1


def test_expired_session_is_refreshed_and_the_rotation_saved(fake_bsky):
    agent = _agent_connected(fake_bsky)
    conn = dict(gateway.store.get_agent(agent["agent_id"]).social_connections["bluesky"])
    gateway.store.set_social_connection(agent["agent_id"], "bluesky", {**conn, "expires_at": int(time.time()) - 10})
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["bluesky"], "message": "Hi"})
    done = client.post(f"/api/v1/approvals/{r.json()['queued'][0]['approval_id']}/approve", json={})
    assert done.status_code == 200, done.text
    [refresh] = [c for c in fake_bsky.calls if c["url"].endswith("refreshSession")]
    assert refresh["bearer"] == "refresh-1" and refresh["url"].startswith("https://pds.example.com/")
    saved = gateway.store.get_agent(agent["agent_id"]).social_connections["bluesky"]
    assert saved["refresh_token"] == "refresh-2" and saved["did"] == DID and saved["handle"] == "city.bsky.social"
    assert fake_bsky.records()[0]["bearer"] == saved["access_token"]


def test_revoked_session_fails_the_approval_with_a_reconnect_hint(fake_bsky):
    agent = _agent_connected(fake_bsky)
    conn = dict(gateway.store.get_agent(agent["agent_id"]).social_connections["bluesky"])
    gateway.store.set_social_connection(agent["agent_id"], "bluesky", {**conn, "expires_at": 0})
    fake_bsky.fail_refresh = "HTTP 400: ExpiredToken"
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["bluesky"], "message": "Hi"})
    aid = r.json()["queued"][0]["approval_id"]
    assert client.post(f"/api/v1/approvals/{aid}/approve", json={}).status_code == 502
    assert "reconnect" in client.get(f"/api/v1/approvals/{aid}").json()["error"].lower()


def test_disconnect_ends_the_session_and_forgets_the_account(fake_bsky):
    agent = _agent_connected(fake_bsky)
    r = client.delete(f"/api/v1/agents/{agent['agent_id']}/social/bluesky")
    assert r.status_code == 200 and r.json()["social_connections"] == {}
    assert any(c["url"].endswith("deleteSession") for c in fake_bsky.calls)


def test_one_agent_can_hold_both_platforms_and_publish_to_each(fake_bsky):
    agent = _agent_connected(fake_bsky)
    gateway.store.set_social_connection(
        agent["agent_id"], "x", {"access_token": "x-tok", "handle": "cityx", "expires_at": 2**40, "refresh_token": "r"}
    )
    r = client.post(
        f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["bluesky", "x", "reddit"], "message": "Hi"}
    )
    assert [q["platform"] for q in r.json()["queued"]] == ["bluesky", "x"]
    assert r.json()["skipped"][0]["platform"] == "reddit"
    view = client.get(f"/api/v1/agents/{agent['agent_id']}").json()["social_connections"]
    assert set(view) == {"bluesky", "x"} and "x-tok" not in str(view)


def test_other_users_cannot_connect_a_bluesky_account_to_my_agent(fake_bsky):
    from tests.helpers import auth

    mine = _agent(auth("00000000-0000-0000-0000-0000000a11ce"), kind="social")
    r = client.post(
        f"/api/v1/agents/{mine['agent_id']}/social/bluesky/connect",
        json={"identifier": "x.bsky.social", "app_password": APP_PW},
        headers=auth("00000000-0000-0000-0000-00000000b0b0"),
    )
    assert r.status_code == 404 and fake_bsky.calls == []


def test_agents_cannot_inject_a_connection_to_post_somewhere_else(fake_bsky):
    agent = _agent_connected(fake_bsky)
    headers, _ = _token(agent["agent_id"])
    out = _call("social.post", {"platform": "bluesky", "text": "Hi", "connection": {"access_token": "evil"}}, headers)
    aid = out.json()["approval_id"]
    assert client.post(f"/api/v1/approvals/{aid}/approve", json={}).status_code == 400
    assert fake_bsky.records() == []


def test_connections_survive_a_dynamodb_round_trip():
    from app.core.dynamodb import table
    from identity_gateway.dynamo_repo import DynamoAgentRepository
    from identity_gateway.store import AgentRecord

    repo = DynamoAgentRepository(table("agents"))
    conn = {"access_token": "a", "refresh_token": "r", "expires_at": 1_900_000_000, "did": DID, "handle": "h", "service": "https://s"}
    repo.insert(AgentRecord(agent_id="agent-dyn-social", name="n", agency="a", tool_grants=set(), social_connections={"bluesky": conn}))
    back = repo.get("agent-dyn-social").social_connections["bluesky"]
    assert back == conn and isinstance(back["expires_at"], int)  # not a Decimal: it's compared to time.time()
    # Rows written before this field existed load with none.
    assert AgentRecord(agent_id="x", name="n", agency="a", tool_grants=set()).social_connections == {}
