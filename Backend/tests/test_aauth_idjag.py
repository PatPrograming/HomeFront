"""AAuth + ID-JAG shaped token exchange and PoP on guarded()."""

from __future__ import annotations

import json
import secrets
import time

import boto3
import jwt
import pytest
from fastapi.testclient import TestClient
from identity_gateway.authz import StarterPolicyAuthorizer
from identity_gateway.gateway import Gateway, set_gateway
from identity_gateway.storage import MandateStore, create_table
from mandate_contracts.ddb import StafferItem
from moto import mock_aws

from aauth import (
    generate_agent_key,
    jwk_thumbprint,
    mint_aa_agent_jwt,
    public_jwk,
    sign_request,
    verify_aa_auth_jwt,
)
from app.main import app
from app.routers.sessions import AgentClientIdentity, StaffIdentity

SECRET = secrets.token_hex(24)
MAYA = StaffIdentity("maya", "sub-maya", "maya@example.gov", frozenset({"staffer"}))
AGENT = AgentClientIdentity("agent-client-1", frozenset({"mandate/tools"}))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1",
        "MANDATE_SIGNING_SECRET": SECRET,
        "AAUTH_SIGNING_KEY": SECRET,
        "HOMEFRONT_AUTH_PROTOCOLS": "legacy,aauth,idjag",
        "HOMEFRONT_LIVE_DATAGOV": "0",
        "HOMEFRONT_LIVE_SCRAPE": "0",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def store():
    with mock_aws():
        table = create_table(boto3.resource("dynamodb", region_name="us-east-1"))
        s = MandateStore(table)
        s.put_staffer("maya", StafferItem(active=True, office="rep-x", role="staffer"))
        yield s


@pytest.fixture
def gateway(store):
    g = Gateway(store, StarterPolicyAuthorizer(), SECRET)
    set_gateway(g)
    return g


@pytest.fixture
def client(gateway):
    from app.routers import oauth, sessions

    app.dependency_overrides.clear()
    app.dependency_overrides[sessions.require_staff] = lambda: MAYA
    app.dependency_overrides[sessions.require_agent_client] = lambda: AGENT
    app.dependency_overrides[oauth.require_agent_client] = lambda: AGENT
    yield TestClient(app)
    app.dependency_overrides.clear()


def _staff_subject() -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": "maya", "token_use": "staff_subject", "iat": now, "exp": now + 600, "groups": ["staffer"]},
        SECRET,
        algorithm="HS256",
    )


def test_well_known_aauth_discovery(client):
    r = client.get("/api/v1/.well-known/aauth-agent.json")
    assert r.status_code == 200
    body = r.json()
    assert "token_endpoint" in body and body["protocols"]["aauth"] is True


def test_idjag_exchange_and_aa_auth_pop_allows_tool(client, gateway):
    sid = client.post("/api/v1/sessions").json()["sid"]
    exchange = client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "requested_token_type": "urn:ietf:params:oauth:token-type:id-jag",
            "subject_token": _staff_subject(),
            "sid": sid,
        },
    )
    assert exchange.status_code == 200, exchange.text
    id_jag = exchange.json()["access_token"]
    header = jwt.get_unverified_header(id_jag)
    assert header["typ"] == "oauth-id-jag+jwt"

    key = generate_agent_key()
    bearer = client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": id_jag,
            "client_jwk": json.dumps(public_jwk(key)),
        },
    )
    assert bearer.status_code == 200, bearer.text
    aa_auth = bearer.json()["access_token"]
    payload = verify_aa_auth_jwt(aa_auth, secret=SECRET, expected_jkt=jwk_thumbprint(public_jwk(key)))
    assert payload["act"] == "maya" and payload["sid"] == sid

    agent_jwt = mint_aa_agent_jwt(key, issuer="http://test/agent", subject="agent-1")
    headers = sign_request(
        key, method="POST", path="/mcp", body=b"", auth_token=aa_auth, agent_jwt=agent_jwt
    )
    outcome = gateway.guarded(
        "datagov_search",
        {"query": "housing"},
        aa_auth,
        headers=headers,
        method="POST",
        path="/mcp",
        body=b"",
    )
    assert outcome.allowed
    assert outcome.event.auth_protocol == "idjag+aauth"


def test_offboard_blocks_next_aauth_call(client, gateway):
    sid = client.post("/api/v1/sessions").json()["sid"]
    id_jag = client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": _staff_subject(),
            "sid": sid,
        },
    ).json()["access_token"]
    key = generate_agent_key()
    aa_auth = client.post(
        "/api/v1/oauth/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": id_jag,
            "client_jwk": json.dumps(public_jwk(key)),
        },
    ).json()["access_token"]

    # Revoke by deactivating the staffer (Offboard path).
    gateway.store.put_staffer("maya", StafferItem(active=False, office="rep-x", role="staffer"))
    agent_jwt = mint_aa_agent_jwt(key, issuer="http://test/agent", subject="agent-1")
    headers = sign_request(
        key, method="POST", path="/mcp", body=b"", auth_token=aa_auth, agent_jwt=agent_jwt
    )
    outcome = gateway.guarded(
        "datagov_search",
        {"query": "x"},
        aa_auth,
        headers=headers,
        method="POST",
        path="/mcp",
        body=b"",
    )
    assert not outcome.allowed
    assert outcome.event.reason == "session revoked / delegator inactive"


def test_legacy_mandate_still_works(client, gateway):
    sid = client.post("/api/v1/sessions").json()["sid"]
    token = client.post(f"/api/v1/sessions/{sid}/mandate").json()["mandateToken"]
    outcome = gateway.guarded("datagov_search", {"query": "x"}, token)
    assert outcome.allowed
    assert outcome.event.auth_protocol == "legacy_mandate"
