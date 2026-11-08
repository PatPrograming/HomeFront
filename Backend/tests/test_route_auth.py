"""Every API route needs our Cognito login, except a short public list.

Walks the app's OpenAPI routes, so a route added later (by us or a teammate) is
checked automatically: it must answer 401 without a login unless it is listed
in PUBLIC below. `/mcp` is not an OpenAPI route; it is authenticated by the
mandate token and tested in packages/identity-gateway/tests/test_gw_mcp.py.
"""

from __future__ import annotations

import secrets

import boto3
import pytest
from fastapi.testclient import TestClient
from identity_gateway import agents_api
from identity_gateway.authz import StarterPolicyAuthorizer
from identity_gateway.gateway import Gateway, set_gateway
from identity_gateway.storage import MandateStore, create_table
from moto import mock_aws

from app.main import app
from tests.helpers import auth

PREFIX = "/api/v1"
PUBLIC_PATHS = {f"{PREFIX}/health", f"{PREFIX}/home", f"{PREFIX}/tools", f"{PREFIX}/demo/matrix"}
PUBLIC_PREFIXES = (f"{PREFIX}/atlas/",)
# Our dashboard tool call: authenticated by a mandate token, checked inside the handler,
# so it needs a valid body to reach that check.
BODIES = {f"{PREFIX}/mcp/call": {"tool": "case_read", "arguments": {"case_id": "case-1"}}}

MAYA = auth("maya", groups=("staffer",))  # a staffer who isn't an admin


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _operations() -> list[tuple[str, str]]:
    return [(method, path) for path, ops in app.openapi()["paths"].items() for method in ops]


def _concrete(path: str) -> str:
    return path.replace("{", "x-").replace("}", "")


def _call(client: TestClient, method: str, path: str, headers=None):
    kwargs = {"headers": headers or {}}
    if method in {"post", "put", "patch"}:
        kwargs["json"] = BODIES.get(path, {})
    return client.request(method.upper(), _concrete(path), **kwargs)


@pytest.fixture
def client(monkeypatch):
    # A configured mandate gateway on moto, so routes reach their auth check instead
    # of failing on a missing AVP store (no AWS is called).
    for key, value in {"AWS_DEFAULT_REGION": "us-east-1", "MANDATE_SIGNING_SECRET": secrets.token_hex(24)}.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        store = MandateStore(create_table(boto3.resource("dynamodb", region_name="us-east-1")))
        set_gateway(Gateway(store, StarterPolicyAuthorizer(), secrets.token_hex(24)))
        app.dependency_overrides.clear()
        yield TestClient(app)


def test_the_route_walk_covers_ours_and_the_teammates_routes():
    paths = {path for _, path in _operations()}
    assert PUBLIC_PATHS <= paths
    for expected in ("/sessions", "/events", "/agents", "/auth/token", "/approvals", "/audit", "/email/account"):
        assert f"{PREFIX}{expected}" in paths


@pytest.mark.parametrize(("method", "path"), [op for op in _operations() if not _is_public(op[1])])
def test_every_non_public_route_is_401_without_a_login(client, method, path):
    r = _call(client, method, path)
    assert r.status_code == 401, f"{method.upper()} {path} -> {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize(("method", "path"), [op for op in _operations() if _is_public(op[1])])
def test_public_routes_answer_without_a_login(client, method, path):
    assert _call(client, method, path).status_code != 401


def _teammate_writes() -> list[tuple[str, str]]:
    return [
        (method.lower(), f"{PREFIX}{route.path}")
        for route in agents_api.router.routes
        for method in route.methods
        if method not in {"GET", "HEAD"}
    ]


@pytest.mark.usefixtures("cognito_login")
@pytest.mark.parametrize(("method", "path"), _teammate_writes())
def test_a_staffer_cannot_use_any_teammate_write_route(client, method, path):
    r = _call(client, method, path, MAYA)
    assert r.status_code == 403, f"{method.upper()} {path} -> {r.status_code}"


@pytest.mark.usefixtures("cognito_login")
def test_auth_token_is_refused_to_a_staffer_and_minted_for_an_admin(client):
    body = {"agent_id": "agent-comms-01"}
    r = client.post(f"{PREFIX}/auth/token", json=body, headers=MAYA)
    assert r.status_code == 403 and "access_token" not in r.text
    r = client.post(f"{PREFIX}/auth/token", json=body, headers=auth("chiefofstaff"))
    assert r.status_code == 200 and r.json()["access_token"]


@pytest.mark.usefixtures("cognito_login")
def test_a_staffer_can_read_the_teammates_tabs(client):
    for path in ("/agents", "/approvals", "/audit", "/agent-types", "/agent-tools", "/email/account"):
        assert client.get(f"{PREFIX}{path}", headers=MAYA).status_code == 200, path
