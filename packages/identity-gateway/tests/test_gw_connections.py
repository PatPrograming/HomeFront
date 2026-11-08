"""Connected accounts, helper level: ConnectionService + the module-level helpers Lane H calls.

Offline: moto DynamoDB + moto KMS (a real key, KmsCipher) and provider HTTP through
httpx.MockTransport. Written from docs/loops/connected-accounts.md "## Interfaces", before
`identity_gateway.connections` exists. The module is imported inside fixtures so every test
fails on its own until the implementation lands.
"""

from __future__ import annotations

import base64
import email as email_lib
import hashlib
import importlib
import json
import logging
import urllib.parse

import boto3
import httpx
import pytest

# Distinctive secrets: if any shows up where it should not, a plain substring check finds it.
CODE = "AUTHCODE-secret-7731"
ACCESS_1 = "ya29.SECRET-ACCESS-ONE"
ACCESS_2 = "ya29.SECRET-ACCESS-TWO"
REFRESH_1 = "1//SECRET-REFRESH-ONE"
X_ACCESS = "xacc.SECRET-X-ACCESS"
X_REFRESH = "xref.SECRET-X-REFRESH"

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
GMAIL_SEND = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
X_AUTH = "https://x.com/i/oauth2/authorize"
X_TOKEN = "https://api.x.com/2/oauth2/token"
X_REVOKE = "https://api.x.com/2/oauth2/revoke"
X_ME = "https://api.x.com/2/users/me"
X_TWEETS = "https://api.x.com/2/tweets"
REDIRECT = "http://localhost:3000/account/connected"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def id_token(email: str) -> str:
    """An unsigned JWT-shaped id_token: the implementation only decodes the payload."""
    header = b64url(json.dumps({"alg": "RS256"}).encode())
    payload = b64url(json.dumps({"email": email, "sub": "g-1"}).encode())
    return f"{header}.{payload}.sig"


class Provider:
    """Fake Google + X. Records every request; knobs let a test make the provider misbehave."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.token_status = 200
        self.refresh_status = 200
        self.send_status = 200
        self.expires_in = 3600
        self.google_email = "maya@gmail.com"
        self.x_username = "mayax"

    def to(self, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).split("?")[0] == url]

    @staticmethod
    def form(request: httpx.Request) -> dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs(request.content.decode()).items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if url == GOOGLE_TOKEN:
            form = self.form(request)
            if form.get("grant_type") == "refresh_token":
                if self.refresh_status != 200:
                    return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
                # Google does not send a new refresh_token on refresh.
                return httpx.Response(200, json={"access_token": ACCESS_2, "expires_in": self.expires_in})
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={
                "access_token": ACCESS_1, "refresh_token": REFRESH_1, "expires_in": self.expires_in,
                "id_token": id_token(self.google_email),
                "scope": "openid email https://www.googleapis.com/auth/gmail.send",
            })
        if url == X_TOKEN:
            form = self.form(request)
            if form.get("grant_type") == "refresh_token":
                if self.refresh_status != 200:
                    return httpx.Response(self.refresh_status, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": X_ACCESS + "-2", "refresh_token": X_REFRESH + "-2",
                                                 "expires_in": self.expires_in})
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": X_ACCESS, "refresh_token": X_REFRESH,
                                             "expires_in": self.expires_in,
                                             "scope": "tweet.read tweet.write users.read offline.access"})
        if url == X_ME:
            return httpx.Response(200, json={"data": {"id": "42", "username": self.x_username}})
        if url == GMAIL_SEND:
            if self.send_status != 200:
                return httpx.Response(self.send_status, json={"error": {"code": self.send_status}})
            return httpx.Response(200, json={"id": "gm-msg-1", "threadId": "t-1"})
        if url == X_TWEETS:
            if self.send_status != 200:
                return httpx.Response(self.send_status, json={"title": "Unauthorized"})
            return httpx.Response(201, json={"data": {"id": "tw-1", "text": "x"}})
        if url in (GOOGLE_REVOKE, X_REVOKE):
            return httpx.Response(200, json={})
        return httpx.Response(500, json={"error": f"unexpected request to {url}"})


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def cx():
    return importlib.import_module("identity_gateway.connections")


@pytest.fixture
def kms_key(dynamodb):  # depends on `dynamodb` so KMS lives inside the same mock_aws
    return boto3.client("kms", region_name="us-east-1").create_key()["KeyMetadata"]["KeyId"]


@pytest.fixture
def env(monkeypatch, kms_key):
    for k, v in {
        "CONNECTIONS_KMS_KEY_ID": kms_key,
        "GOOGLE_OAUTH_CLIENT_ID": "google-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "google-client-secret",
        "X_OAUTH_CLIENT_ID": "x-client-id",
        "X_OAUTH_CLIENT_SECRET": "x-client-secret",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CONNECTIONS_REDIRECT_URI", raising=False)


@pytest.fixture
def provider():
    return Provider()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def staffers(store):
    from mandate_contracts.ddb import StafferItem

    store.put_staffer("sam", StafferItem(active=True, office="rep-x", role="staffer"))
    return store


@pytest.fixture
def svc(cx, env, staffers, provider, clock, kms_key):
    cipher = cx.KmsCipher(boto3.client("kms", region_name="us-east-1"), kms_key)
    service = cx.ConnectionService(
        staffers, cipher, httpx.Client(transport=httpx.MockTransport(provider)), cx.OAuthConfig.from_env(), clock=clock
    )
    cx.set_connections(service)
    yield service
    cx.set_connections(None)


def state_of(url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["state"][0]


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


def connect(svc, staffer="maya", provider_id="gmail"):
    url = svc.start(staffer, provider_id)
    return svc.finish(staffer, CODE, state_of(url))


def raw_items(table):
    return table.scan(ConsistentRead=True)["Items"]


def connection_item(table, staffer="maya", provider_id="gmail"):
    return table.get_item(Key={"pk": f"CONNECTION#{staffer}", "sk": f"PROVIDER#{provider_id}"}, ConsistentRead=True).get("Item")


# ---- require_connection ---------------------------------------------------------------------


def test_require_raises_needs_connection_when_no_row(svc, cx):
    with pytest.raises(cx.NeedsConnection) as exc:
        svc.require("maya", "gmail")
    assert exc.value.provider == "gmail"


def test_require_returns_connection_with_handle_after_finish(svc, cx):
    connect(svc)
    conn = svc.require("maya", "gmail")
    assert isinstance(conn, cx.Connection)
    assert (conn.staffer, conn.provider, conn.handle) == ("maya", "gmail", "maya@gmail.com")
    assert conn.scopes == cx.SCOPES["gmail"]


def test_connection_object_carries_no_token(svc):
    connect(svc)
    conn = svc.require("maya", "gmail")
    fields = set(vars(conn))
    assert not any("token" in f or "secret" in f for f in fields), fields
    assert ACCESS_1 not in repr(conn) and REFRESH_1 not in repr(conn)


def test_x_handle_comes_from_users_me(svc):
    connect(svc, provider_id="x")
    assert svc.require("maya", "x").handle == "@mayax"


def test_require_for_unsupported_provider_needs_connection(svc, cx):
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "linkedin")


def test_inactive_staffer_needs_connection_even_with_a_row(svc, cx, store):
    connect(svc)
    store.set_staffer_active("maya", False)
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "gmail")


def test_unknown_staffer_needs_connection_even_with_a_row(svc, cx, table):
    connect(svc)
    item = connection_item(table)
    table.put_item(Item={**item, "pk": "CONNECTION#ghost"})
    with pytest.raises(cx.NeedsConnection):
        svc.require("ghost", "gmail")


def test_connection_is_per_staffer(svc, cx):
    connect(svc)
    with pytest.raises(cx.NeedsConnection):
        svc.require("sam", "gmail")


# ---- encryption at rest ---------------------------------------------------------------------


def test_stored_row_never_holds_the_plaintext_token(svc, table):
    connect(svc)
    connect(svc, provider_id="x")
    dump = json.dumps(raw_items(table), default=str)
    for secret in (ACCESS_1, REFRESH_1, X_ACCESS, X_REFRESH):
        assert secret not in dump
    row = connection_item(table)
    assert row["handle"] == "maya@gmail.com"
    assert row["ciphertext"]  # Binary blob present


def test_ciphertext_copied_to_another_staffer_cannot_be_decrypted(svc, cx, table):
    connect(svc)
    row = connection_item(table)
    table.put_item(Item={**row, "pk": "CONNECTION#sam"})
    with pytest.raises(cx.NeedsConnection):
        svc.require("sam", "gmail")


def test_ciphertext_copied_to_another_provider_cannot_be_decrypted(svc, cx, table):
    connect(svc)
    row = connection_item(table)
    table.put_item(Item={**row, "sk": "PROVIDER#x"})
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "x")


def test_kms_cipher_binds_the_encryption_context(cx, kms_key):
    cipher = cx.KmsCipher(boto3.client("kms", region_name="us-east-1"), kms_key)
    blob = cipher.encrypt(b"hello", {"staffer": "maya", "provider": "gmail"})
    assert b"hello" not in blob
    assert cipher.decrypt(blob, {"staffer": "maya", "provider": "gmail"}) == b"hello"
    with pytest.raises(Exception):
        cipher.decrypt(blob, {"staffer": "sam", "provider": "gmail"})


# ---- refresh --------------------------------------------------------------------------------


def test_unexpired_token_is_not_refreshed(svc, provider, clock):
    connect(svc)
    clock.now += 100
    svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    assert not any(Provider.form(r).get("grant_type") == "refresh_token" for r in provider.to(GOOGLE_TOKEN))
    assert provider.to(GMAIL_SEND)[0].headers["authorization"] == f"Bearer {ACCESS_1}"


def test_expired_access_token_is_refreshed_and_the_new_token_used(svc, provider, clock):
    connect(svc)
    clock.now += 3600 - 30  # inside the 60 s refresh window
    svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    refreshes = [r for r in provider.to(GOOGLE_TOKEN) if Provider.form(r).get("grant_type") == "refresh_token"]
    assert len(refreshes) == 1
    form = Provider.form(refreshes[0])
    assert form["refresh_token"] == REFRESH_1
    assert form["client_id"] == "google-client-id"
    assert provider.to(GMAIL_SEND)[-1].headers["authorization"] == f"Bearer {ACCESS_2}"


def test_refreshed_token_is_stored_and_the_old_refresh_token_kept(svc, provider, clock):
    connect(svc)
    clock.now += 3600
    svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    clock.now += 3600
    svc.send_gmail("maya", "jane@example.com", "Hi again", "Body")
    refreshes = [Provider.form(r) for r in provider.to(GOOGLE_TOKEN) if Provider.form(r).get("grant_type") == "refresh_token"]
    assert len(refreshes) == 2
    assert all(f["refresh_token"] == REFRESH_1 for f in refreshes)


def test_refresh_failure_needs_connection_deletes_row_and_sends_nothing(svc, cx, provider, clock, table):
    connect(svc)
    clock.now += 3600
    provider.refresh_status = 400
    with pytest.raises(cx.NeedsConnection):
        svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    assert provider.to(GMAIL_SEND) == []
    assert connection_item(table) is None


def test_provider_401_on_send_needs_connection_and_deletes_row(svc, cx, provider, table):
    connect(svc)
    provider.send_status = 401
    with pytest.raises(cx.NeedsConnection):
        svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    assert connection_item(table) is None
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "gmail")


def test_provider_401_on_x_post_needs_connection(svc, cx, provider):
    connect(svc, provider_id="x")
    provider.send_status = 401
    with pytest.raises(cx.NeedsConnection):
        svc.post_x("maya", "hello")


def test_provider_500_on_send_is_provider_error_not_needs_connection(svc, cx, provider):
    connect(svc)
    provider.send_status = 500
    with pytest.raises(cx.ProviderError):
        svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    svc.require("maya", "gmail")  # still connected


# ---- senders --------------------------------------------------------------------------------


def test_send_gmail_builds_base64url_rfc2822_message_with_bearer(svc, provider):
    connect(svc)
    result = svc.send_gmail("maya", "jane@example.com", "Pothole update", "We fixed Main St.")
    assert result == {"status": "sent", "messageId": "gm-msg-1"}
    req = provider.to(GMAIL_SEND)[0]
    assert req.method == "POST"
    assert req.headers["authorization"] == f"Bearer {ACCESS_1}"
    raw = json.loads(req.content)["raw"]
    assert "+" not in raw and "/" not in raw  # url-safe alphabet
    msg = email_lib.message_from_bytes(unb64url(raw))
    assert msg["To"] == "jane@example.com"
    assert msg["Subject"] == "Pothole update"
    assert "We fixed Main St." in msg.get_payload(decode=True).decode()


def test_send_gmail_without_connection_calls_no_provider(svc, cx, provider):
    with pytest.raises(cx.NeedsConnection):
        svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    assert provider.requests == []


def test_post_x_posts_text_with_bearer(svc, provider):
    connect(svc, provider_id="x")
    result = svc.post_x("maya", "Town hall Friday")
    assert result == {"status": "posted", "id": "tw-1"}
    req = provider.to(X_TWEETS)[0]
    assert req.headers["authorization"] == f"Bearer {X_ACCESS}"
    body = json.loads(req.content)
    assert body["text"] == "Town hall Friday"
    assert "reply" not in body


def test_post_x_reply_sets_in_reply_to_tweet_id(svc, provider):
    connect(svc, provider_id="x")
    svc.post_x("maya", "Thanks!", reply_to="1234567890")
    assert json.loads(provider.to(X_TWEETS)[0].content)["reply"] == {"in_reply_to_tweet_id": "1234567890"}


def test_gmail_connection_cannot_post_to_x(svc, cx, provider):
    connect(svc)
    with pytest.raises(cx.NeedsConnection):
        svc.post_x("maya", "hello")
    assert provider.to(X_TWEETS) == []


# ---- start() --------------------------------------------------------------------------------


def test_start_gmail_builds_authorize_url_with_pkce(svc, cx, table):
    url = svc.start("maya", "gmail")
    assert url.startswith(GOOGLE_AUTH + "?")
    q = query_of(url)
    assert q["response_type"] == "code"
    assert q["client_id"] == "google-client-id"
    assert q["redirect_uri"] == REDIRECT
    assert q["scope"] == cx.SCOPES["gmail"]
    assert q["access_type"] == "offline"
    assert q["code_challenge_method"] == "S256"
    row = table.get_item(Key={"pk": f"OAUTHSTATE#{q['state']}", "sk": "STATE"}, ConsistentRead=True)["Item"]
    assert (row["staffer"], row["provider"]) == ("maya", "gmail")
    assert q["code_challenge"] == b64url(hashlib.sha256(row["verifier"].encode()).digest())
    assert 43 <= len(row["verifier"]) <= 128
    assert row["verifier"] not in url
    assert int(row["expires"]) == 1_800_000_000 + 600


def test_start_x_builds_authorize_url_with_pkce(svc, cx):
    url = svc.start("maya", "x")
    assert url.startswith(X_AUTH + "?")
    q = query_of(url)
    assert q["response_type"] == "code" and q["client_id"] == "x-client-id"
    assert q["scope"] == cx.SCOPES["x"]
    assert q["code_challenge_method"] == "S256" and q["state"] and q["code_challenge"]


def test_each_start_gets_a_fresh_unguessable_state(svc):
    states = {state_of(svc.start("maya", "gmail")) for _ in range(5)}
    assert len(states) == 5
    assert all(len(s) >= 20 for s in states)


def test_redirect_uri_comes_from_env_when_set(cx, env, staffers, provider, kms_key, monkeypatch):
    monkeypatch.setenv("CONNECTIONS_REDIRECT_URI", "https://app.example.gov/account/connected")
    svc = cx.ConnectionService(
        staffers, cx.KmsCipher(boto3.client("kms", region_name="us-east-1"), kms_key),
        httpx.Client(transport=httpx.MockTransport(provider)), cx.OAuthConfig.from_env(),
    )
    assert query_of(svc.start("maya", "gmail"))["redirect_uri"] == "https://app.example.gov/account/connected"


def test_start_without_kms_key_is_not_configured(cx, env, staffers, provider, monkeypatch):
    monkeypatch.delenv("CONNECTIONS_KMS_KEY_ID")
    svc = cx.ConnectionService(staffers, None, httpx.Client(transport=httpx.MockTransport(provider)), cx.OAuthConfig.from_env())
    with pytest.raises(cx.NotConfigured):
        svc.start("maya", "gmail")


def test_start_without_client_id_is_not_configured(cx, env, staffers, provider, kms_key, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID")
    svc = cx.ConnectionService(
        staffers, cx.KmsCipher(boto3.client("kms", region_name="us-east-1"), kms_key),
        httpx.Client(transport=httpx.MockTransport(provider)), cx.OAuthConfig.from_env(),
    )
    with pytest.raises(cx.NotConfigured):
        svc.start("maya", "gmail")
    svc.start("maya", "x")  # the other provider is still set up


@pytest.mark.parametrize("pid", ["linkedin", "facebook", "bluesky"])
def test_start_for_unsupported_provider_is_not_configured(svc, cx, pid):
    with pytest.raises(cx.NotConfigured):
        svc.start("maya", pid)


def test_list_shows_all_five_providers_with_flags(svc):
    rows = {r["provider"]: r for r in svc.list("maya")}
    assert set(rows) == {"gmail", "x", "linkedin", "facebook", "bluesky"}
    assert rows["gmail"]["label"] == "Gmail"
    assert rows["gmail"]["supported"] and rows["gmail"]["configured"] and not rows["gmail"]["connected"]
    assert not rows["linkedin"]["supported"] and not rows["linkedin"]["configured"]


def test_list_after_connect_shows_handle_and_never_a_token(svc):
    connect(svc)
    row = {r["provider"]: r for r in svc.list("maya")}["gmail"]
    assert row["connected"] and row["handle"] == "maya@gmail.com" and row["scopes"]
    assert row["connectedAt"]
    dump = json.dumps(svc.list("maya"), default=str)
    for secret in (ACCESS_1, REFRESH_1, "ciphertext"):
        assert secret not in dump


# ---- finish(): state handling ---------------------------------------------------------------


def test_finish_exchanges_code_with_verifier_and_secret(svc, provider, table):
    url = svc.start("maya", "gmail")
    state = state_of(url)
    verifier = table.get_item(Key={"pk": f"OAUTHSTATE#{state}", "sk": "STATE"})["Item"]["verifier"]
    svc.finish("maya", CODE, state)
    form = Provider.form(provider.to(GOOGLE_TOKEN)[0])
    assert form["grant_type"] == "authorization_code" and form["code"] == CODE
    assert form["code_verifier"] == verifier
    assert form["client_secret"] == "google-client-secret" and form["redirect_uri"] == REDIRECT


def test_x_token_exchange_uses_basic_client_auth(svc, provider):
    connect(svc, provider_id="x")
    req = provider.to(X_TOKEN)[0]
    expected = base64.b64encode(b"x-client-id:x-client-secret").decode()
    assert req.headers["authorization"] == f"Basic {expected}"


def test_state_is_single_use(svc, cx, provider):
    state = state_of(svc.start("maya", "gmail"))
    svc.finish("maya", CODE, state)
    calls = len(provider.to(GOOGLE_TOKEN))
    with pytest.raises(cx.OAuthStateError):
        svc.finish("maya", CODE, state)
    assert len(provider.to(GOOGLE_TOKEN)) == calls


def test_unknown_state_is_rejected(svc, cx, provider):
    with pytest.raises(cx.OAuthStateError):
        svc.finish("maya", CODE, "forged-state-value")
    assert provider.to(GOOGLE_TOKEN) == []


def test_empty_state_is_rejected(svc, cx):
    with pytest.raises(cx.OAuthStateError):
        svc.finish("maya", CODE, "")


def test_expired_state_is_rejected_and_consumed(svc, cx, provider, clock, table):
    state = state_of(svc.start("maya", "gmail"))
    clock.now += 601
    with pytest.raises(cx.OAuthStateError):
        svc.finish("maya", CODE, state)
    assert provider.to(GOOGLE_TOKEN) == []
    assert table.get_item(Key={"pk": f"OAUTHSTATE#{state}", "sk": "STATE"}).get("Item") is None


def test_state_just_inside_ten_minutes_still_works(svc, clock):
    state = state_of(svc.start("maya", "gmail"))
    clock.now += 599
    assert svc.finish("maya", CODE, state).handle == "maya@gmail.com"


def test_other_staffers_state_is_rejected_and_consumed(svc, cx, provider):
    state = state_of(svc.start("maya", "gmail"))
    with pytest.raises(cx.OAuthStateError):
        svc.finish("sam", CODE, state)
    assert provider.to(GOOGLE_TOKEN) == []
    with pytest.raises(cx.OAuthStateError):  # consumed: maya cannot use it afterwards either
        svc.finish("maya", CODE, state)
    with pytest.raises(cx.NeedsConnection):
        svc.require("sam", "gmail")
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "gmail")


def test_failed_token_exchange_stores_nothing(svc, cx, provider, table):
    provider.token_status = 400
    state = state_of(svc.start("maya", "gmail"))
    with pytest.raises(cx.ProviderError):
        svc.finish("maya", CODE, state)
    assert connection_item(table) is None


# ---- disconnect and offboarding -------------------------------------------------------------


def test_disconnect_revokes_at_provider_and_deletes_row(svc, cx, provider, table):
    connect(svc)
    assert svc.disconnect("maya", "gmail") is True
    revokes = provider.to(GOOGLE_REVOKE)
    assert len(revokes) == 1
    form = dict(urllib.parse.parse_qsl(revokes[0].content.decode()))
    assert form["token"] == REFRESH_1  # the refresh token: revoking it ends the whole grant
    assert form["token_type_hint"] == "refresh_token"
    assert connection_item(table) is None
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "gmail")


def test_disconnect_when_provider_revoke_fails_still_deletes_row(cx, env, staffers, provider, clock, kms_key, table):
    def flaky(request):
        if str(request.url).startswith(GOOGLE_REVOKE):
            raise httpx.ConnectError("down")
        return provider(request)

    svc = cx.ConnectionService(
        staffers, cx.KmsCipher(boto3.client("kms", region_name="us-east-1"), kms_key),
        httpx.Client(transport=httpx.MockTransport(flaky)), cx.OAuthConfig.from_env(), clock=clock,
    )
    connect(svc)
    svc.disconnect("maya", "gmail")
    assert connection_item(table) is None


def test_disconnect_only_touches_that_staffers_row(svc, cx):
    connect(svc, "maya")
    connect(svc, "sam")
    svc.disconnect("sam", "gmail")
    assert svc.require("maya", "gmail").handle
    with pytest.raises(cx.NeedsConnection):
        svc.require("sam", "gmail")


def test_disconnect_when_not_connected_is_harmless(svc):
    svc.disconnect("maya", "gmail")  # must not raise


def test_revoke_all_removes_every_connection_of_that_staffer_only(svc, provider, table):
    connect(svc, "maya")
    connect(svc, "maya", "x")
    connect(svc, "sam")
    assert svc.revoke_all("maya") == 2
    assert connection_item(table, "maya", "gmail") is None and connection_item(table, "maya", "x") is None
    assert len(provider.to(GOOGLE_REVOKE)) == 1 and len(provider.to(X_REVOKE)) == 1
    assert svc.require("sam", "gmail")
    assert svc.revoke_all("maya") == 0


# ---- provider_for_tool ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool,args,expected",
    [
        ("email_send", {}, "gmail"),
        ("email_send_list", {"listId": "l1"}, "gmail"),
        ("social_post", {}, "x"),
        ("social_post", {"platform": None}, "x"),
        ("social_post", {"platform": ""}, "x"),
        ("social_post", {"platform": "x"}, "x"),
        ("social_reply", {}, "x"),
        ("social_reply", {"platform": "x"}, "x"),
        ("social_post", {"platform": "linkedin"}, None),
        ("social_post", {"platform": "facebook"}, None),
        ("social_post", {"platform": "bluesky"}, None),
        ("social_reply", {"platform": "linkedin"}, None),
        ("inbox_read", {}, None),
        ("case_lookup", {"caseId": "case-1"}, None),
    ],
)
def test_provider_for_tool(cx, tool, args, expected):
    assert cx.provider_for_tool(tool, args) == expected


# ---- needs_connection_result ----------------------------------------------------------------


def test_needs_connection_result_shape(cx):
    result = cx.needs_connection_result(cx.NeedsConnection("gmail"))
    assert result["status"] == "needs_connection"
    assert result["provider"] == "gmail" and result["label"] == "Gmail"
    assert result["connectUrl"] == "/account?connect=gmail"
    assert result["note"].startswith("NOT sent")
    assert "Account page" in result["note"]


def test_needs_connection_result_for_x(cx):
    result = cx.needs_connection_result(cx.NeedsConnection("x"))
    assert (result["provider"], result["label"], result["connectUrl"]) == ("x", "X", "/account?connect=x")


def test_needs_connection_result_and_exception_carry_no_token(svc, cx, provider):
    connect(svc)
    provider.send_status = 401
    with pytest.raises(cx.NeedsConnection) as exc:
        svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    blob = json.dumps(cx.needs_connection_result(exc.value)) + str(exc.value) + repr(exc.value)
    for secret in (ACCESS_1, REFRESH_1, CODE):
        assert secret not in blob


# ---- guarded()-shaped harness (Lane H's wiring) ---------------------------------------------


def make_guarded(cx, sender_calls):
    """Mimics how Lane H wires the helper into guarded(): look up the provider for the tool,
    require the connection first, and only then run the sender."""

    def guarded(staffer, tool, args):
        provider_id = cx.provider_for_tool(tool, args)
        if provider_id:
            try:
                cx.require_connection(staffer, provider_id)
            except cx.NeedsConnection as e:
                return cx.needs_connection_result(e)
        sender_calls.append((staffer, tool))
        if tool == "email_send":
            return cx.send_gmail(staffer, args["to"], args["subject"], args["body"])
        if tool == "social_post":
            return cx.post_x(staffer, args["text"])
        return {"status": "ok"}

    return guarded


def test_guarded_harness_never_sends_without_a_connection(svc, cx, provider):
    calls: list = []
    guarded = make_guarded(cx, calls)
    result = guarded("maya", "email_send", {"to": "jane@example.com", "subject": "Hi", "body": "Body"})
    assert result["status"] == "needs_connection" and result["provider"] == "gmail"
    assert calls == []
    assert provider.requests == []

    connect(svc)
    result = guarded("maya", "email_send", {"to": "jane@example.com", "subject": "Hi", "body": "Body"})
    assert result["status"] == "sent"
    assert calls == [("maya", "email_send")]
    assert len(provider.to(GMAIL_SEND)) == 1


def test_guarded_harness_social_post_needs_x_not_gmail(svc, cx, provider):
    calls: list = []
    guarded = make_guarded(cx, calls)
    connect(svc)  # Gmail connected, X is not
    result = guarded("maya", "social_post", {"text": "hello"})
    assert result["status"] == "needs_connection" and result["provider"] == "x"
    assert calls == [] and provider.to(X_TWEETS) == []


def test_guarded_harness_lets_non_sending_tools_through(svc, cx):
    calls: list = []
    assert make_guarded(cx, calls)("maya", "inbox_read", {})["status"] == "ok"
    assert calls == [("maya", "inbox_read")]


def test_guarded_harness_offboarded_staffer_is_stopped_even_with_a_row(svc, cx, store, provider):
    calls: list = []
    guarded = make_guarded(cx, calls)
    connect(svc)
    store.set_staffer_active("maya", False)
    result = guarded("maya", "email_send", {"to": "jane@example.com", "subject": "Hi", "body": "Body"})
    assert result["status"] == "needs_connection"
    assert calls == [] and provider.to(GMAIL_SEND) == []


def test_module_level_helpers_use_the_installed_service(svc, cx):
    connect(svc)
    assert cx.get_connections() is svc
    assert cx.require_connection("maya", "gmail").handle == "maya@gmail.com"
    assert cx.send_gmail("maya", "jane@example.com", "Hi", "Body")["status"] == "sent"


# ---- logging --------------------------------------------------------------------------------


def test_nothing_logs_a_token_code_or_verifier(env, svc, clock, table, caplog):
    caplog.set_level(logging.DEBUG)
    url = svc.start("maya", "gmail")
    state = state_of(url)
    verifier = table.get_item(Key={"pk": f"OAUTHSTATE#{state}", "sk": "STATE"})["Item"]["verifier"]
    svc.finish("maya", CODE, state)
    svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    clock.now += 3600
    svc.send_gmail("maya", "jane@example.com", "Hi again", "Body")
    connect(svc, provider_id="x")
    svc.post_x("maya", "hello")
    svc.disconnect("maya", "gmail")
    text = caplog.text
    for secret in (ACCESS_1, ACCESS_2, REFRESH_1, X_ACCESS, X_REFRESH, CODE, verifier):
        assert secret not in text


# ==== round-1 review gaps (G1-G3, G5-G7, G9) ==================================================

import botocore.exceptions  # noqa: E402

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


def build(cx, staffers, provider, clock, kms_key, override=None):
    """A service whose HTTP goes through `override(request)` first; it returns a Response or None to fall through."""

    def handler(request):
        if override is not None:
            handled = override(request)
            if handled is not None:
                return handled
        return provider(request)

    return cx.ConnectionService(
        staffers, cx.KmsCipher(boto3.client("kms", region_name="us-east-1"), kms_key),
        httpx.Client(transport=httpx.MockTransport(handler)), cx.OAuthConfig.from_env(), clock=clock,
    )


def is_refresh(request):
    return str(request.url).split("?")[0] in (GOOGLE_TOKEN, X_TOKEN) and Provider.form(request).get("grant_type") == "refresh_token"


# ---- G1: refresh must not resurrect a disconnected row -----------------------------------------


def test_refresh_after_a_concurrent_disconnect_does_not_recreate_the_row(env, cx, staffers, provider, clock, kms_key, table):
    def disconnect_mid_refresh(request):
        if is_refresh(request):
            table.delete_item(Key={"pk": "CONNECTION#maya", "sk": "PROVIDER#gmail"})

    svc = build(cx, staffers, provider, clock, kms_key, disconnect_mid_refresh)
    connect(svc)
    clock.now += 3600
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "gmail")
    assert connection_item(table) is None
    assert [i for i in raw_items(table) if i["pk"].startswith("CONNECTION#")] == []


def test_send_after_a_concurrent_disconnect_sends_nothing_and_list_still_works(env, cx, staffers, provider, clock, kms_key, table):
    def disconnect_mid_refresh(request):
        if is_refresh(request):
            table.delete_item(Key={"pk": "CONNECTION#maya", "sk": "PROVIDER#gmail"})

    svc = build(cx, staffers, provider, clock, kms_key, disconnect_mid_refresh)
    connect(svc)
    clock.now += 3600
    with pytest.raises(cx.NeedsConnection):
        svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    assert provider.to(GMAIL_SEND) == []
    assert {r["provider"]: r for r in svc.list("maya")}["gmail"]["connected"] is False  # must not raise


# ---- G2: a failed refresh must not delete a row someone else already refreshed ------------------


def test_refresh_failure_keeps_a_row_that_a_parallel_refresh_already_replaced(env, cx, staffers, provider, clock, kms_key, table):
    holder = {}

    def parallel_winner_then_400(request):
        if is_refresh(request):
            winner = holder["svc"]._encrypt("maya", "gmail", {
                "access_token": "winner-access", "refresh_token": REFRESH_1, "expires_at": clock.now + 3600,
            })
            holder["winner"] = winner
            table.update_item(
                Key={"pk": "CONNECTION#maya", "sk": "PROVIDER#gmail"},
                UpdateExpression="SET ciphertext = :c", ExpressionAttributeValues={":c": winner},
            )
            return httpx.Response(400, json={"error": "invalid_grant"})

    svc = holder["svc"] = build(cx, staffers, provider, clock, kms_key, parallel_winner_then_400)
    connect(svc)
    clock.now += 3600
    try:
        svc.require("maya", "gmail")
    except (cx.NeedsConnection, cx.ProviderError):
        pass
    row = connection_item(table)
    assert row is not None, "the winner's freshly refreshed row was deleted by the loser's failed refresh"
    assert row["ciphertext"] == holder["winner"]


# ---- G3: disconnect always deletes our copy -----------------------------------------------------


def _kms_unreachable(*args, **kwargs):
    raise botocore.exceptions.EndpointConnectionError(endpoint_url="x")


def test_disconnect_deletes_the_row_even_when_kms_is_unreachable(svc, table, monkeypatch):
    connect(svc)
    monkeypatch.setattr(svc.cipher, "decrypt", _kms_unreachable)
    assert svc.disconnect("maya", "gmail") is True
    assert connection_item(table) is None


def test_revoke_all_continues_across_providers_when_kms_is_unreachable(svc, table, monkeypatch):
    connect(svc, "maya", "gmail")
    connect(svc, "maya", "x")
    monkeypatch.setattr(svc.cipher, "decrypt", _kms_unreachable)
    assert svc.revoke_all("maya") == 2
    assert connection_item(table, "maya", "gmail") is None
    assert connection_item(table, "maya", "x") is None


# ---- G5: finish checks the granted scopes and the shape of the token response ------------------


def token_override(url, body):
    """Replace the (non-refresh) token-endpoint 200 body for `url`."""

    def override(request):
        if str(request.url).split("?")[0] == url and not is_refresh(request):
            return httpx.Response(200, json=body)

    return override


def google_body(**changes):
    body = {
        "access_token": ACCESS_1, "refresh_token": REFRESH_1, "expires_in": 3600,
        "id_token": id_token("maya@gmail.com"), "scope": f"openid email {GMAIL_SEND_SCOPE}",
    }
    body.update(changes)
    return {k: v for k, v in body.items() if v is not None}


def x_body(**changes):
    body = {
        "access_token": X_ACCESS, "refresh_token": X_REFRESH, "expires_in": 7200,
        "scope": "tweet.read tweet.write users.read offline.access",
    }
    body.update(changes)
    return {k: v for k, v in body.items() if v is not None}


def test_gmail_grant_without_gmail_send_is_rejected_nothing_stored_and_revoked(env, cx, staffers, provider, clock, kms_key, table):
    svc = build(cx, staffers, provider, clock, kms_key, token_override(GOOGLE_TOKEN, google_body(scope="openid email")))
    with pytest.raises(cx.ProviderError):
        connect(svc)
    assert connection_item(table) is None
    revokes = provider.to(GOOGLE_REVOKE)
    assert len(revokes) == 1  # the grant we will not use is handed back
    assert dict(urllib.parse.parse_qsl(revokes[0].content.decode()))["token"] in (ACCESS_1, REFRESH_1)


def test_x_grant_without_tweet_write_is_rejected_nothing_stored_and_revoked(env, cx, staffers, provider, clock, kms_key, table):
    body = x_body(scope="tweet.read users.read offline.access")
    svc = build(cx, staffers, provider, clock, kms_key, token_override(X_TOKEN, body))
    with pytest.raises(cx.ProviderError):
        connect(svc, provider_id="x")
    assert connection_item(table, "maya", "x") is None
    assert len(provider.to(X_REVOKE)) == 1


def test_stored_scopes_are_the_scopes_actually_granted(env, cx, staffers, provider, clock, kms_key, table):
    granted = f"openid https://www.googleapis.com/auth/userinfo.email {GMAIL_SEND_SCOPE}"
    svc = build(cx, staffers, provider, clock, kms_key, token_override(GOOGLE_TOKEN, google_body(scope=granted)))
    conn = connect(svc)
    assert set(conn.scopes.split()) == set(granted.split())
    assert set(connection_item(table)["scopes"].split()) == set(granted.split())
    assert set(svc.require("maya", "gmail").scopes.split()) == set(granted.split())


@pytest.mark.parametrize("missing", ["id_token", "access_token", "expires_in"])
def test_gmail_token_response_missing_a_field_is_provider_error(env, cx, staffers, provider, clock, kms_key, table, missing):
    svc = build(cx, staffers, provider, clock, kms_key, token_override(GOOGLE_TOKEN, google_body(**{missing: None})))
    with pytest.raises(cx.ProviderError):
        connect(svc)
    assert connection_item(table) is None


@pytest.mark.parametrize("missing", ["access_token", "expires_in"])
def test_x_token_response_missing_a_field_is_provider_error(env, cx, staffers, provider, clock, kms_key, table, missing):
    svc = build(cx, staffers, provider, clock, kms_key, token_override(X_TOKEN, x_body(**{missing: None})))
    with pytest.raises(cx.ProviderError):
        connect(svc, provider_id="x")
    assert connection_item(table, "maya", "x") is None


# ---- G6: clean up the grant when a later step fails; only invalid_grant deletes ------------------


def test_failed_x_handle_lookup_revokes_the_fresh_token_and_stores_nothing(env, cx, staffers, provider, clock, kms_key, table):
    def me_500(request):
        if str(request.url).split("?")[0] == X_ME:
            return httpx.Response(500, json={})

    svc = build(cx, staffers, provider, clock, kms_key, me_500)
    with pytest.raises(cx.ProviderError):
        connect(svc, provider_id="x")
    assert connection_item(table, "maya", "x") is None
    revokes = provider.to(X_REVOKE)
    assert len(revokes) == 1
    assert dict(urllib.parse.parse_qsl(revokes[0].content.decode()))["token"] in (X_ACCESS, X_REFRESH)


def test_refresh_rejected_with_400_tries_to_revoke_the_refresh_token(svc, cx, provider, clock):
    connect(svc)
    clock.now += 3600
    provider.refresh_status = 400
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "gmail")
    revokes = provider.to(GOOGLE_REVOKE)
    assert len(revokes) == 1
    assert dict(urllib.parse.parse_qsl(revokes[0].content.decode()))["token"] == REFRESH_1


def test_refresh_rejected_with_401_needs_connection_and_deletes_the_row(svc, cx, provider, clock, table):
    connect(svc)
    clock.now += 3600
    provider.refresh_status = 401
    with pytest.raises(cx.NeedsConnection):
        svc.require("maya", "gmail")
    assert connection_item(table) is None


@pytest.mark.parametrize("status", [429, 500, 503])
def test_refresh_hitting_a_provider_outage_is_provider_error_and_keeps_the_row(svc, cx, provider, clock, table, status):
    connect(svc)
    clock.now += 3600
    provider.refresh_status = status
    with pytest.raises(cx.ProviderError):
        svc.send_gmail("maya", "jane@example.com", "Hi", "Body")
    assert connection_item(table) is not None
    assert provider.to(GMAIL_SEND) == []
    provider.refresh_status = 200
    svc.send_gmail("maya", "jane@example.com", "Hi", "Body")  # still connected; works once the provider recovers


# ---- G7 ------------------------------------------------------------------------------------


def test_needs_connection_result_for_an_unknown_provider_does_not_raise_and_labels_with_the_id(cx):
    result = cx.needs_connection_result(cx.NeedsConnection("myspace"))
    assert result["status"] == "needs_connection"
    assert result["provider"] == "myspace" and result["label"] == "myspace"


# ---- G9: importing must not lower a stricter SDK log level ---------------------------------------


@pytest.mark.parametrize("sdk_logger", ["botocore", "boto3"])
@pytest.mark.parametrize("before", [logging.WARNING, logging.ERROR, logging.DEBUG])
def test_importing_connections_never_lowers_an_sdk_log_level(cx, sdk_logger, before):
    import importlib.util
    import sys

    logger = logging.getLogger(sdk_logger)
    saved = logger.level
    name = "identity_gateway._connections_log_probe"  # a private copy, so the real module and its classes stay intact
    try:
        logger.setLevel(before)
        spec = importlib.util.spec_from_file_location(name, cx.__file__)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        if before == logging.DEBUG:
            assert logger.level >= logging.INFO
        else:
            assert logger.level == before
    finally:
        sys.modules.pop(name, None)
        logger.setLevel(saved)
