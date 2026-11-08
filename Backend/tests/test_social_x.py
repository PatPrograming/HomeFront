"""Connecting an X account and posting to it after approval. X's API is faked at
x_api._request, so no test reaches the network."""
import time
import urllib.parse
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app  # puts packages/*/src on sys.path
from tests.test_approvals import _agent, _call, _token

from identity_gateway import app as gateway  # noqa: E402
from identity_gateway import social  # noqa: E402
from mcp_tools import x_api  # noqa: E402

client = TestClient(app, follow_redirects=False)


class FakeX:
    """Records calls and answers like X's API."""

    def __init__(self):
        self.calls = []
        self.fail_post = None

    def __call__(self, url, *, method="GET", form=None, payload=None, bearer=None, basic=False):
        self.calls.append({"url": url, "method": method, "form": form, "payload": payload, "bearer": bearer})
        if url == x_api.TOKEN_URL:
            n = len([c for c in self.calls if c["url"] == x_api.TOKEN_URL])
            return {
                "access_token": f"access-{n}",
                "refresh_token": f"refresh-{n}",
                "expires_in": 7200,
                "scope": "tweet.read tweet.write users.read offline.access",
            }
        if url == x_api.ME_URL:
            return {"data": {"id": "42", "username": "cityofexample", "name": "City of Example"}}
        if url == x_api.TWEETS_URL:
            if self.fail_post:
                raise x_api.XApiError(self.fail_post, 403)
            return {"data": {"id": "1900000000000000001"}}
        if url == x_api.REVOKE_URL:
            return {"revoked": True}
        raise AssertionError(f"unexpected X call: {url}")

    def posted(self):
        return [c for c in self.calls if c["url"] == x_api.TWEETS_URL]


@pytest.fixture
def fake_x(monkeypatch):
    fake = FakeX()
    monkeypatch.setattr(x_api, "_request", fake)
    monkeypatch.setenv("X_CLIENT_ID", "test-x-client")
    monkeypatch.setenv("X_REDIRECT_URI", "http://127.0.0.1:8080/api/v1/social/x/callback")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    return fake


def _social_agent(**body):
    return _agent(kind="social", **body)


def _connect(agent_id, fake):
    """Run the whole consent flow; returns the callback's redirect response."""
    r = client.post(f"/api/v1/agents/{agent_id}/social/x/connect")
    assert r.status_code == 200, r.text
    url = urllib.parse.urlparse(r.json()["authorize_url"])
    q = dict(urllib.parse.parse_qsl(url.query))
    assert url.netloc == "x.com" and q["code_challenge_method"] == "S256"
    assert q["client_id"] == "test-x-client" and "tweet.write" in q["scope"]
    return client.get("/api/v1/social/x/callback", params={"state": q["state"], "code": "auth-code"}), q


def test_connect_stores_the_account_and_never_exposes_tokens(fake_x):
    agent = _social_agent()
    done, q = _connect(agent["agent_id"], fake_x)
    assert done.status_code == 303
    assert done.headers["location"].startswith("http://localhost:3000/social-platform?")
    assert "connected=x" in done.headers["location"]

    # The code exchange carried the PKCE verifier matching the challenge sent to X.
    [exchange] = [c for c in fake_x.calls if c["url"] == x_api.TOKEN_URL]
    assert exchange["form"]["code"] == "auth-code"
    import base64, hashlib

    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(exchange["form"]["code_verifier"].encode()).digest()
    ).rstrip(b"=").decode()
    assert challenge == q["code_challenge"]

    view = client.get(f"/api/v1/agents/{agent['agent_id']}").json()
    assert view["social_connections"]["x"]["handle"] == "cityofexample"
    flat = str(view) + client.get(f"/api/v1/agents/{agent['agent_id']}/social").text
    assert "access-1" not in flat and "refresh-1" not in flat
    assert [e["type"] for e in client.get("/api/v1/audit", params={"agent_id": agent["agent_id"]}).json()][0] == "social_connected"


def test_state_is_single_use_and_unknown_states_are_rejected(fake_x):
    agent = _social_agent()
    _, q = _connect(agent["agent_id"], fake_x)
    replay = client.get("/api/v1/social/x/callback", params={"state": q["state"], "code": "again"})
    assert "social_error=state_expired" in replay.headers["location"]
    forged = client.get("/api/v1/social/x/callback", params={"state": "nope", "code": "c"})
    assert "social_error=state_expired" in forged.headers["location"]


def test_declined_consent_connects_nothing(fake_x):
    agent = _social_agent()
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/social/x/connect")
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.json()["authorize_url"]).query))["state"]
    back = client.get("/api/v1/social/x/callback", params={"state": state, "error": "access_denied"})
    assert "social_error=denied" in back.headers["location"]
    assert client.get(f"/api/v1/agents/{agent['agent_id']}").json()["social_connections"] == {}


def test_connect_needs_x_to_be_configured(monkeypatch):
    monkeypatch.setenv("X_CLIENT_ID", "")
    agent = _social_agent()
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/social/x/connect")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "x_not_configured"


def test_publish_queues_then_approval_posts_for_real(fake_x):
    agent = _social_agent()
    _connect(agent["agent_id"], fake_x)
    r = client.post(
        f"/api/v1/agents/{agent['agent_id']}/social/publish",
        json={"platforms": ["x", "facebook"], "message": "Town hall Tuesday 6pm.", "links": ["https://city.gov/hall"]},
    )
    assert r.status_code == 202, r.text
    out = r.json()
    [queued] = out["queued"]
    assert queued["platform"] == "x"
    assert out["skipped"] == [{"platform": "facebook", "reason": "not_supported", "message": "Not available yet."}]
    assert fake_x.posted() == []  # nothing goes out before approval

    pending = client.get(f"/api/v1/approvals/{queued['approval_id']}").json()
    assert pending["social"] == {"platform": "x", "account": "@cityofexample", "simulated": False}
    assert pending["arguments"]["text"] == "Town hall Tuesday 6pm.\n\nhttps://city.gov/hall"

    done = client.post(f"/api/v1/approvals/{queued['approval_id']}/approve", json={})
    assert done.status_code == 200, done.text
    result = done.json()["result"]
    assert result["simulated"] is False and result["account"] == "@cityofexample"
    assert result["url"].endswith("/status/1900000000000000001")
    [post] = fake_x.posted()
    assert post["bearer"] == "access-1" and post["payload"]["text"].startswith("Town hall")
    assert "access-1" not in str(done.json())  # the token isn't echoed into the approval


def test_publish_requires_a_connection_and_the_grant(fake_x):
    agent = _social_agent()
    r = client.post(
        f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["x"], "message": "Hi"}
    )
    assert r.json()["queued"] == [] and r.json()["skipped"][0]["reason"] == "not_connected"

    no_grant = _social_agent()
    assert client.post(f"/api/v1/agents/{no_grant['agent_id']}/grants/social.post/revoke").status_code == 200
    r = client.post(f"/api/v1/agents/{no_grant['agent_id']}/social/publish", json={"platforms": ["x"], "message": "Hi"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "tool_not_granted"


def test_publish_rejects_posts_over_the_x_limit(fake_x):
    agent = _social_agent()
    _connect(agent["agent_id"], fake_x)
    r = client.post(
        f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["x"], "message": "a" * 300}
    )
    assert r.json()["queued"] == [] and r.json()["skipped"][0]["reason"] == "too_long"
    # A long URL counts as 23 characters, like X counts it.
    r = client.post(
        f"/api/v1/agents/{agent['agent_id']}/social/publish",
        json={"platforms": ["x"], "message": "Update", "links": ["https://city.gov/" + "p" * 200]},
    )
    assert len(r.json()["queued"]) == 1


def test_failed_post_is_recorded_not_reported_as_sent(fake_x):
    agent = _social_agent()
    _connect(agent["agent_id"], fake_x)
    fake_x.fail_post = "HTTP 403: You are not allowed to create a Tweet with duplicate content."
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["x"], "message": "Hi"})
    aid = r.json()["queued"][0]["approval_id"]
    done = client.post(f"/api/v1/approvals/{aid}/approve", json={})
    assert done.status_code == 502
    assert client.get(f"/api/v1/approvals/{aid}").json()["status"] == "failed"


def test_expired_access_token_is_refreshed_and_the_rotation_saved(fake_x):
    agent = _social_agent()
    _connect(agent["agent_id"], fake_x)
    conn = dict(gateway.store.get_agent(agent["agent_id"]).social_connections["x"])
    gateway.store.set_social_connection(agent["agent_id"], "x", {**conn, "expires_at": int(time.time()) - 10})

    r = client.post(f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["x"], "message": "Hi"})
    done = client.post(f"/api/v1/approvals/{r.json()['queued'][0]['approval_id']}/approve", json={})
    assert done.status_code == 200, done.text
    refresh_call = [c for c in fake_x.calls if c["form"] and c["form"].get("grant_type") == "refresh_token"]
    assert refresh_call and refresh_call[0]["form"]["refresh_token"] == "refresh-1"
    assert fake_x.posted()[0]["bearer"] == "access-2"
    saved = gateway.store.get_agent(agent["agent_id"]).social_connections["x"]
    assert saved["access_token"] == "access-2" and saved["refresh_token"] == "refresh-2"


def test_revoked_refresh_token_fails_the_approval_with_a_reconnect_hint(fake_x, monkeypatch):
    agent = _social_agent()
    _connect(agent["agent_id"], fake_x)
    conn = dict(gateway.store.get_agent(agent["agent_id"]).social_connections["x"])
    gateway.store.set_social_connection(agent["agent_id"], "x", {**conn, "expires_at": 0})

    def refuse(url, **kw):
        raise x_api.XApiError("HTTP 400: invalid_request", 400)

    monkeypatch.setattr(x_api, "_request", refuse)
    r = client.post(f"/api/v1/agents/{agent['agent_id']}/social/publish", json={"platforms": ["x"], "message": "Hi"})
    done = client.post(f"/api/v1/approvals/{r.json()['queued'][0]['approval_id']}/approve", json={})
    assert done.status_code == 502
    assert "reconnect" in client.get(f"/api/v1/approvals/{r.json()['queued'][0]['approval_id']}").json()["error"].lower()


def test_disconnect_removes_the_account_and_revokes_the_token(fake_x):
    agent = _social_agent()
    _connect(agent["agent_id"], fake_x)
    r = client.delete(f"/api/v1/agents/{agent['agent_id']}/social/x")
    assert r.status_code == 200 and r.json()["social_connections"] == {}
    assert any(c["url"] == x_api.REVOKE_URL for c in fake_x.calls)
    assert client.delete(f"/api/v1/agents/{agent['agent_id']}/social/x").status_code == 404


def test_without_a_connection_an_approved_post_is_simulated():
    agent = _social_agent()
    headers, _ = _token(agent["agent_id"])
    out = _call("social.post", {"platform": "x", "text": "Hello"}, headers).json()
    done = client.post(f"/api/v1/approvals/{out['approval_id']}/approve", json={}).json()
    assert done["result"]["simulated"] is True and "Connect one" in done["result"]["hint"]


def test_agents_cannot_pass_a_connection_or_token_as_arguments(fake_x):
    agent = _social_agent()
    headers, _ = _token(agent["agent_id"])
    r = _call("social.post", {"platform": "x", "text": "Hi", "token": "stolen"}, headers)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "secret_probe_blocked"
    r = _call("social.post", {"platform": "x", "text": "Hi", "connection": {"access_token": "x"}}, headers)
    aid = r.json()["approval_id"]
    # Reviewer approval validates against the tool schema, which has no `connection` field.
    assert client.post(f"/api/v1/approvals/{aid}/approve", json={}).status_code == 400


def test_other_users_cannot_connect_or_publish_for_my_agent(fake_x):
    from tests.helpers import auth

    alice = auth("00000000-0000-0000-0000-0000000a11ce")
    bob = auth("00000000-0000-0000-0000-00000000b0b0")
    mine = _agent(alice, kind="social")
    assert client.post(f"/api/v1/agents/{mine['agent_id']}/social/x/connect", headers=bob).status_code == 404
    assert client.post(
        f"/api/v1/agents/{mine['agent_id']}/social/publish", json={"platforms": ["x"], "message": "Hi"}, headers=bob
    ).status_code == 404


def test_platforms_endpoint_reports_what_is_live(fake_x):
    out = client.get("/api/v1/social/platforms").json()
    assert out["live"] == ["bluesky", "x"] and out["configured"]["x"] is True
    assert out["configured"]["bluesky"] is True and out["limits"] == {"x": 280, "bluesky": 300}


def test_unused_pending_states_expire():
    social._pending["old"] = social.PendingConnect("a", "x", "v", None, time.time() - 1)
    assert social.take_pending("old") is None
