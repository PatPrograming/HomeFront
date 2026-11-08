"""Connected accounts, HTTP API: /api/v1/connections* and the offboarding hook.

Goes through the real FastAPI app with real staff Cognito-shaped tokens (`cognito_login`),
moto DynamoDB + moto KMS, and provider HTTP through httpx.MockTransport. Written from
docs/loops/connected-accounts.md "## Interfaces" before the router exists. These are the
security gate: every test names a guard that must make it fail when removed.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.parse

import boto3
import httpx
import pytest
from fastapi.testclient import TestClient
from identity_gateway.authz import StarterPolicyAuthorizer
from identity_gateway.gateway import Gateway, set_gateway
from identity_gateway.storage import MandateStore, create_table
from mandate_contracts.ddb import StafferItem
from moto import mock_aws

from app.main import app
from tests.helpers import auth, user_token

SIGNING_SECRET = secrets.token_hex(24)
CODE = "AUTHCODE-secret-7731"
ACCESS_1 = "ya29.SECRET-ACCESS-ONE"
REFRESH_1 = "1//SECRET-REFRESH-ONE"
X_ACCESS = "xacc.SECRET-X-ACCESS"
X_REFRESH = "xref.SECRET-X-REFRESH"

GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
X_TOKEN = "https://api.x.com/2/oauth2/token"
X_REVOKE = "https://api.x.com/2/oauth2/revoke"
X_ME = "https://api.x.com/2/users/me"

MAYA = auth("maya", groups=("staffer",))
SAM = auth("sam", groups=("staffer",))
CHIEF = auth("chiefofstaff", groups=("admin",))

PROVIDERS = {"gmail", "x", "linkedin", "facebook", "bluesky"}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _id_token(email: str) -> str:
    return f"{_b64url(b'{}')}.{_b64url(json.dumps({'email': email}).encode())}.sig"


class Provider:
    """Fake Google + X token, userinfo and revoke endpoints; records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.token_status = 200
        self.override: dict[str, dict] = {}  # token-endpoint url -> JSON body to return with 200
        self.me_body: dict | None = None  # X /users/me JSON body to return with 200 instead of the default

    def to(self, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).split("?")[0] == url]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        if url in (GOOGLE_TOKEN, X_TOKEN):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            if url in self.override:
                return httpx.Response(200, json=self.override[url])
            if url == GOOGLE_TOKEN:
                return httpx.Response(200, json={
                    "access_token": ACCESS_1, "refresh_token": REFRESH_1, "expires_in": 3600,
                    "id_token": _id_token("maya@gmail.com"),
                    "scope": "openid email https://www.googleapis.com/auth/gmail.send",
                })
            return httpx.Response(200, json={"access_token": X_ACCESS, "refresh_token": X_REFRESH, "expires_in": 7200,
                                             "scope": "tweet.read tweet.write users.read offline.access"})
        if url == X_ME:
            if self.me_body is not None:
                return httpx.Response(200, json=self.me_body)
            return httpx.Response(200, json={"data": {"id": "42", "username": "mayax"}})
        if url in (GOOGLE_REVOKE, X_REVOKE):
            return httpx.Response(200, json={})
        return httpx.Response(500, json={"error": f"unexpected request to {url}"})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "MANDATE_SIGNING_SECRET": SIGNING_SECRET,
        "HOMEFRONT_LIVE_DATAGOV": "0",
        "HOMEFRONT_LIVE_SCRAPE": "0",
        "GOOGLE_OAUTH_CLIENT_ID": "google-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "google-client-secret",
        "X_OAUTH_CLIENT_ID": "x-client-id",
        "X_OAUTH_CLIENT_SECRET": "x-client-secret",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CONNECTIONS_REDIRECT_URI", raising=False)


@pytest.fixture
def table():
    with mock_aws():
        yield create_table(boto3.resource("dynamodb", region_name="us-east-1"))


@pytest.fixture
def store(table):
    s = MandateStore(table)
    for name, role in (("maya", "staffer"), ("sam", "staffer"), ("chiefofstaff", "admin")):
        s.put_staffer(name, StafferItem(active=True, office="rep-x", role=role))
    return s


@pytest.fixture
def provider():
    return Provider()


@pytest.fixture
def cx():
    import importlib

    return importlib.import_module("identity_gateway.connections")


@pytest.fixture
def svc(cx, store, provider, monkeypatch):
    kms_key = boto3.client("kms", region_name="us-east-1").create_key()["KeyMetadata"]["KeyId"]
    monkeypatch.setenv("CONNECTIONS_KMS_KEY_ID", kms_key)
    service = cx.ConnectionService(
        store,
        cx.KmsCipher(boto3.client("kms", region_name="us-east-1"), kms_key),
        httpx.Client(transport=httpx.MockTransport(provider)),
        cx.OAuthConfig.from_env(),
    )
    cx.set_connections(service)
    set_gateway(Gateway(store, StarterPolicyAuthorizer(), SIGNING_SECRET))
    yield service
    cx.set_connections(None)
    set_gateway(None)


@pytest.fixture
def client(cognito_login, svc):
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def state_of(authorize_url: str) -> str:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(authorize_url).query)["state"][0]


def connect(client, who=MAYA, pid="gmail"):
    r = client.post(f"/api/v1/connections/{pid}/start", json={}, headers=who)
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/connections/callback", json={"code": CODE, "state": state_of(r.json()["authorizeUrl"])}, headers=who)
    assert r.status_code == 200, r.text
    return r.json()


def listing(client, who=MAYA) -> dict[str, dict]:
    r = client.get("/api/v1/connections", headers=who)
    assert r.status_code == 200, r.text
    return {c["provider"]: c for c in r.json()["connections"]}


def connection_rows(table):
    return [i for i in table.scan(ConsistentRead=True)["Items"] if i["pk"].startswith("CONNECTION#")]


# ---- authentication: staff only ------------------------------------------------------------


ROUTES = [
    ("get", "/api/v1/connections", None),
    ("post", "/api/v1/connections/gmail/start", {}),
    ("post", "/api/v1/connections/callback", {"code": "c", "state": "s"}),
    ("delete", "/api/v1/connections/gmail", None),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_no_token_is_401(client, method, path, body):
    r = getattr(client, method)(path, **({"json": body} if body is not None else {}))
    assert r.status_code == 401


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_agent_client_credentials_token_is_401(client, method, path, body):
    agent = {"Authorization": "Bearer " + user_token("agent-client", client_id="agent-client-id", scope="mandate/tools")}
    r = getattr(client, method)(path, headers=agent, **({"json": body} if body is not None else {}))
    assert r.status_code == 401


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_mandate_token_is_401(client, method, path, body):
    from identity_gateway.tokens import mint_mandate_token
    from mandate_contracts import MandateClaims

    now = int(time.time())
    claims = MandateClaims(sub="agent-1", act="maya", office="rep-x", mode="official", sid="sid-1", iat=now, exp=now + 900)
    headers = {"Authorization": "Bearer " + mint_mandate_token(claims, SIGNING_SECRET)}
    r = getattr(client, method)(path, headers=headers, **({"json": body} if body is not None else {}))
    assert r.status_code == 401


def test_token_for_a_user_in_no_staff_group_is_403(client):
    assert client.get("/api/v1/connections", headers=auth("nobody", groups=())).status_code == 403


# ---- list ----------------------------------------------------------------------------------


def test_list_shows_all_five_providers_with_flags(client):
    rows = listing(client)
    assert set(rows) == PROVIDERS
    for pid in ("gmail", "x"):
        assert rows[pid]["supported"] and rows[pid]["configured"] and not rows[pid]["connected"]
    for pid in ("linkedin", "facebook", "bluesky"):
        assert not rows[pid]["supported"] and not rows[pid]["configured"] and not rows[pid]["connected"]
    assert rows["gmail"]["label"] == "Gmail"


# ---- start ---------------------------------------------------------------------------------


def test_start_returns_authorize_url_for_the_caller(client, table):
    r = client.post("/api/v1/connections/gmail/start", json={}, headers=MAYA)
    assert r.status_code == 200
    url = r.json()["authorizeUrl"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    state = state_of(url)
    row = table.get_item(Key={"pk": f"OAUTHSTATE#{state}", "sk": "STATE"})["Item"]
    assert (row["staffer"], row["provider"]) == ("maya", "gmail")  # from the token, not the request


def test_start_unknown_provider_is_404(client):
    assert client.post("/api/v1/connections/myspace/start", json={}, headers=MAYA).status_code == 404


@pytest.mark.parametrize("pid", ["linkedin", "facebook", "bluesky"])
def test_start_for_unsupported_provider_is_409_not_set_up(client, pid):
    r = client.post(f"/api/v1/connections/{pid}/start", json={}, headers=MAYA)
    assert r.status_code == 409
    assert r.json()["detail"] == "not_set_up"


def test_start_when_provider_not_configured_is_409_not_set_up(cx, client, store, provider, monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID")
    cx.set_connections(cx.ConnectionService(store, None, httpx.Client(transport=httpx.MockTransport(provider)), cx.OAuthConfig.from_env()))
    r = client.post("/api/v1/connections/gmail/start", json={}, headers=MAYA)
    assert r.status_code == 409 and r.json()["detail"] == "not_set_up"
    assert listing(client)["gmail"]["configured"] is False


# ---- connect, isolation --------------------------------------------------------------------


def test_full_connect_shows_the_handle_in_the_list(client):
    entry = connect(client)
    assert entry["provider"] == "gmail" and entry["connected"] and entry["handle"] == "maya@gmail.com"
    row = listing(client)["gmail"]
    assert row["connected"] and row["handle"] == "maya@gmail.com" and row["connectedAt"]


def test_x_connect_shows_the_at_handle(client):
    assert connect(client, pid="x")["handle"] == "@mayax"


def test_another_staffers_list_does_not_show_my_connection(client):
    connect(client, MAYA)
    assert listing(client, SAM)["gmail"]["connected"] is False
    assert listing(client, MAYA)["gmail"]["connected"] is True


def test_delete_as_sam_leaves_mayas_connection_alone(client, table):
    connect(client, MAYA)
    r = client.delete("/api/v1/connections/gmail", headers=SAM)
    assert r.status_code == 200 and r.json() == {"provider": "gmail", "connected": False}
    assert listing(client, MAYA)["gmail"]["connected"] is True
    assert [i["pk"] for i in connection_rows(table)] == ["CONNECTION#maya"]


def test_connection_is_stored_under_the_token_username_only(client, table):
    connect(client, SAM)
    assert [i["pk"] for i in connection_rows(table)] == ["CONNECTION#sam"]


# ---- the callback's state guards -----------------------------------------------------------


def test_callback_with_forged_state_is_400_invalid_state(client, provider, table):
    r = client.post("/api/v1/connections/callback", json={"code": CODE, "state": "forged"}, headers=MAYA)
    assert r.status_code == 400 and r.json()["detail"] == "invalid_state"
    assert provider.to(GOOGLE_TOKEN) == [] and connection_rows(table) == []


def test_callback_without_state_is_rejected(client, provider):
    r = client.post("/api/v1/connections/callback", json={"code": CODE}, headers=MAYA)
    assert r.status_code in (400, 422)
    assert provider.requests == []


def test_callback_with_empty_state_is_400(client, provider):
    r = client.post("/api/v1/connections/callback", json={"code": CODE, "state": ""}, headers=MAYA)
    assert r.status_code in (400, 422)
    assert provider.requests == []


def test_callback_replayed_state_is_400(client, provider):
    start = client.post("/api/v1/connections/gmail/start", json={}, headers=MAYA).json()
    state = state_of(start["authorizeUrl"])
    body = {"code": CODE, "state": state}
    assert client.post("/api/v1/connections/callback", json=body, headers=MAYA).status_code == 200
    calls = len(provider.to(GOOGLE_TOKEN))
    r = client.post("/api/v1/connections/callback", json=body, headers=MAYA)
    assert r.status_code == 400 and r.json()["detail"] == "invalid_state"
    assert len(provider.to(GOOGLE_TOKEN)) == calls


def test_callback_with_mayas_state_used_by_sam_is_400_and_connects_nobody(client, provider, table):
    start = client.post("/api/v1/connections/gmail/start", json={}, headers=MAYA).json()
    state = state_of(start["authorizeUrl"])
    r = client.post("/api/v1/connections/callback", json={"code": CODE, "state": state}, headers=SAM)
    assert r.status_code == 400 and r.json()["detail"] == "invalid_state"
    assert provider.to(GOOGLE_TOKEN) == []
    assert listing(client, MAYA)["gmail"]["connected"] is False
    assert listing(client, SAM)["gmail"]["connected"] is False
    assert connection_rows(table) == []
    # The state was consumed by the failed attempt, so Maya cannot finish with it either.
    again = client.post("/api/v1/connections/callback", json={"code": CODE, "state": state}, headers=MAYA)
    assert again.status_code == 400


def test_callback_token_exchange_failure_is_502_and_stores_nothing(client, provider, table):
    provider.token_status = 400
    start = client.post("/api/v1/connections/gmail/start", json={}, headers=MAYA).json()
    r = client.post("/api/v1/connections/callback", json={"code": CODE, "state": state_of(start["authorizeUrl"])}, headers=MAYA)
    assert r.status_code == 502
    assert connection_rows(table) == []
    assert listing(client)["gmail"]["connected"] is False
    for secret in (CODE, "invalid_grant"):
        assert secret not in r.text


# ---- no secrets leave the server -----------------------------------------------------------


def test_no_api_response_contains_a_token_verifier_code_or_ciphertext(client, table):
    bodies = []
    start = client.post("/api/v1/connections/gmail/start", json={}, headers=MAYA)
    bodies.append(start.text)
    state = state_of(start.json()["authorizeUrl"])
    verifier = table.get_item(Key={"pk": f"OAUTHSTATE#{state}", "sk": "STATE"})["Item"]["verifier"]
    bodies.append(client.post("/api/v1/connections/callback", json={"code": CODE, "state": state}, headers=MAYA).text)
    bodies.append(client.get("/api/v1/connections", headers=MAYA).text)
    bodies.append(client.delete("/api/v1/connections/gmail", headers=MAYA).text)
    bodies.append(client.get("/api/v1/connections", headers=MAYA).text)
    joined = "\n".join(bodies)
    for secret in (ACCESS_1, REFRESH_1, verifier, CODE, "ciphertext", "client_secret", "google-client-secret"):
        assert secret not in joined, secret


def test_no_token_lands_anywhere_in_dynamodb_except_as_ciphertext(client, table):
    connect(client, MAYA)
    connect(client, MAYA, "x")
    for item in table.scan(ConsistentRead=True)["Items"]:
        if item["pk"].startswith("CONNECTION#"):
            continue
        dump = json.dumps(item, default=str)
        for secret in (ACCESS_1, REFRESH_1, X_ACCESS, X_REFRESH, CODE):
            assert secret not in dump, (item["pk"], secret)
    rows = connection_rows(table)
    assert len(rows) == 2
    for row in rows:
        dump = json.dumps(row, default=str)
        for secret in (ACCESS_1, REFRESH_1, X_ACCESS, X_REFRESH):
            assert secret not in dump


# ---- disconnect ----------------------------------------------------------------------------


def test_disconnect_revokes_at_provider_and_the_assistant_loses_access(client, cx, provider, table):
    connect(client, MAYA)
    r = client.delete("/api/v1/connections/gmail", headers=MAYA)
    assert r.status_code == 200 and r.json() == {"provider": "gmail", "connected": False}
    assert len(provider.to(GOOGLE_REVOKE)) == 1
    assert connection_rows(table) == []
    with pytest.raises(cx.NeedsConnection):
        cx.require_connection("maya", "gmail")
    assert listing(client)["gmail"]["connected"] is False


def test_disconnect_is_idempotent(client):
    assert client.delete("/api/v1/connections/gmail", headers=MAYA).status_code == 200
    assert client.delete("/api/v1/connections/gmail", headers=MAYA).status_code == 200


def test_connected_account_is_usable_by_the_gateway_helper(client, cx):
    connect(client, MAYA)
    assert cx.require_connection("maya", "gmail").handle == "maya@gmail.com"
    with pytest.raises(cx.NeedsConnection):
        cx.require_connection("sam", "gmail")


# ---- offboarding ---------------------------------------------------------------------------


def test_offboarding_revokes_the_staffers_connections(client, cx, provider, table, store):
    connect(client, MAYA)
    connect(client, MAYA, "x")
    connect(client, SAM)
    r = client.post("/api/v1/staff/maya/offboard", json={}, headers=CHIEF)
    assert r.status_code == 200, r.text
    assert r.json()["revokedConnections"] >= 1
    # Rows are gone and the providers were told, not just "staffer inactive".
    assert [i["pk"] for i in connection_rows(table)] == ["CONNECTION#sam"]
    assert len(provider.to(GOOGLE_REVOKE)) >= 1 and len(provider.to(X_REVOKE)) >= 1
    with pytest.raises(cx.NeedsConnection):
        cx.require_connection("maya", "gmail")
    store.set_staffer_active("maya", True)  # even re-activated, the old grant must be gone
    with pytest.raises(cx.NeedsConnection):
        cx.require_connection("maya", "gmail")
    assert cx.require_connection("sam", "gmail")


def test_offboarding_a_staffer_with_no_connections_reports_zero(client):
    r = client.post("/api/v1/staff/sam/offboard", json={}, headers=CHIEF)
    assert r.status_code == 200 and r.json()["revokedConnections"] == 0


def test_a_staffer_cannot_offboard_so_cannot_revoke_others_connections(client, cx):
    connect(client, SAM)
    assert client.post("/api/v1/staff/sam/offboard", json={}, headers=MAYA).status_code == 403
    assert cx.require_connection("sam", "gmail")


# ==== round-1 review gaps (G4, G5) ============================================================


class FakeSupervisor:
    def __init__(self):
        self.stopped: list[tuple[str, str]] = []

    def stop_staffer(self, staffer_id, reason):
        self.stopped.append((staffer_id, reason))


@pytest.fixture
def supervisor():
    from app.services import agent_supervisor

    fake = FakeSupervisor()
    agent_supervisor.set_supervisor(fake)
    yield fake
    agent_supervisor.set_supervisor(None)


def test_offboarding_still_completes_when_revoking_connections_blows_up(client, svc, store, supervisor, monkeypatch):
    connect(client, MAYA)

    def boom(staffer):
        raise RuntimeError("unexpected failure while revoking")

    monkeypatch.setattr(svc, "revoke_all", boom)
    r = client.post("/api/v1/staff/maya/offboard", json={}, headers=CHIEF)
    assert r.status_code == 200, r.text
    assert store.get_staffer("maya").active is False
    assert supervisor.stopped and supervisor.stopped[0][0] == "maya"


def test_offboarding_deletes_the_rows_it_can_even_if_one_provider_fails(client, svc, table, store, supervisor, monkeypatch):
    connect(client, MAYA)
    connect(client, MAYA, "x")
    real = svc.disconnect

    def flaky(staffer, provider_id):
        if provider_id == "gmail":
            raise RuntimeError("unexpected failure on gmail")
        return real(staffer, provider_id)

    monkeypatch.setattr(svc, "disconnect", flaky)
    r = client.post("/api/v1/staff/maya/offboard", json={}, headers=CHIEF)
    assert r.status_code == 200, r.text
    assert store.get_staffer("maya").active is False
    assert supervisor.stopped and supervisor.stopped[0][0] == "maya"
    assert [i for i in connection_rows(table) if i["sk"] == "PROVIDER#x"] == []


def _callback(client, pid="gmail"):
    start = client.post(f"/api/v1/connections/{pid}/start", json={}, headers=MAYA).json()
    return client.post("/api/v1/connections/callback", json={"code": CODE, "state": state_of(start["authorizeUrl"])}, headers=MAYA)


def test_callback_when_gmail_send_was_not_granted_is_a_plain_error_and_stores_nothing(client, provider, table):
    provider.override[GOOGLE_TOKEN] = {
        "access_token": ACCESS_1, "refresh_token": REFRESH_1, "expires_in": 3600,
        "id_token": _id_token("maya@gmail.com"), "scope": "openid email",
    }
    r = _callback(client)
    assert r.status_code == 403
    assert r.json()["detail"].startswith("missing_permission")
    assert r.json()["detail"] != "Connected"
    assert connection_rows(table) == []
    assert listing(client)["gmail"]["connected"] is False
    assert len(provider.to(GOOGLE_REVOKE)) == 1
    for secret in (ACCESS_1, REFRESH_1):
        assert secret not in r.text


def test_callback_when_tweet_write_was_not_granted_is_a_plain_error_and_stores_nothing(client, provider, table):
    provider.override[X_TOKEN] = {
        "access_token": X_ACCESS, "refresh_token": X_REFRESH, "expires_in": 7200, "scope": "tweet.read users.read offline.access",
    }
    r = _callback(client, "x")
    assert r.status_code == 403
    assert r.json()["detail"].startswith("missing_permission")
    assert connection_rows(table) == []
    assert len(provider.to(X_REVOKE)) == 1


@pytest.mark.parametrize("missing", ["id_token", "access_token", "expires_in"])
def test_callback_with_an_incomplete_google_token_response_is_a_clean_error_not_a_500(client, provider, table, missing):
    body = {
        "access_token": ACCESS_1, "refresh_token": REFRESH_1, "expires_in": 3600,
        "id_token": _id_token("maya@gmail.com"), "scope": "openid email https://www.googleapis.com/auth/gmail.send",
    }
    del body[missing]
    provider.override[GOOGLE_TOKEN] = body
    r = _callback(client)
    assert r.status_code in (400, 502)
    assert connection_rows(table) == []


# ==== round-2 review gaps (R5, R7) ============================================================


def _jwt(payload_part: str) -> str:
    return f"{_b64url(b'{}')}.{payload_part}.sig"


@pytest.mark.parametrize(
    "bad_id_token",
    ["notajwt", _jwt("a"), _jwt(_b64url(b"this is not json")), _jwt(_b64url(json.dumps({"sub": "g-1"}).encode()))],
    ids=["not-three-parts", "bad-base64", "payload-not-json", "no-email"],
)
def test_callback_with_a_malformed_id_token_is_502_and_stores_nothing(client, provider, table, bad_id_token):
    provider.override[GOOGLE_TOKEN] = {
        "access_token": ACCESS_1, "refresh_token": REFRESH_1, "expires_in": 3600,
        "id_token": bad_id_token, "scope": "openid email https://www.googleapis.com/auth/gmail.send",
    }
    r = _callback(client)
    assert r.status_code == 502
    assert connection_rows(table) == []
    assert len(provider.to(GOOGLE_REVOKE)) == 1
    for secret in (ACCESS_1, REFRESH_1):
        assert secret not in r.text


def test_callback_when_x_users_me_returns_an_empty_body_is_502_and_the_token_is_revoked(client, provider, table):
    provider.me_body = {}
    r = _callback(client, "x")
    assert r.status_code == 502
    assert connection_rows(table) == []
    assert len(provider.to(X_REVOKE)) == 1


def test_offboard_reports_cleanup_failed_when_one_providers_disconnect_raises(client, svc, store, supervisor, monkeypatch):
    connect(client, MAYA)
    connect(client, MAYA, "x")
    real = svc.disconnect

    def flaky(staffer, provider_id):
        if provider_id == "gmail":
            raise RuntimeError("unexpected failure on gmail")
        return real(staffer, provider_id)

    monkeypatch.setattr(svc, "disconnect", flaky)
    r = client.post("/api/v1/staff/maya/offboard", json={}, headers=CHIEF)
    assert r.status_code == 200, r.text
    assert r.json()["connectionCleanupFailed"] is True


def test_offboard_reports_cleanup_not_failed_when_everything_was_removed(client, supervisor):
    connect(client, MAYA)
    connect(client, MAYA, "x")
    r = client.post("/api/v1/staff/maya/offboard", json={}, headers=CHIEF)
    assert r.status_code == 200, r.text
    assert r.json()["connectionCleanupFailed"] is False


def test_offboard_reports_cleanup_failed_when_revoke_all_itself_raises(client, svc, supervisor, monkeypatch):
    connect(client, MAYA)

    def boom(staffer):
        raise RuntimeError("unexpected failure while revoking")

    monkeypatch.setattr(svc, "revoke_all", boom)
    r = client.post("/api/v1/staff/maya/offboard", json={}, headers=CHIEF)
    assert r.status_code == 200, r.text
    assert r.json()["connectionCleanupFailed"] is True
