import os

# Empty Cognito ids beat a real .env file, so tests never fetch the real pool's keys.
# (Values already exported in the shell still win: run the suite from a clean shell.)
# Tests that need a working login opt in with the `cognito_login` fixture below.
os.environ.setdefault("COGNITO_USER_POOL_ID", "")
os.environ.setdefault("COGNITO_STAFF_CLIENT_ID", "")
os.environ.setdefault("COGNITO_AGENT_CLIENT_ID", "")

# The teammates' agent API (identity_gateway.agents_api): fake AWS credentials, an
# in-memory agent store, no Bedrock runner and never a real email sender, whatever a
# developer .env says (env vars beat the .env file). Must run before the app is imported.
os.environ.update(
    {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_REGION": "us-east-1",
        "DYNAMODB_TABLE_PREFIX": "test-",
        "DYNAMODB_ENDPOINT_URL": "",
        "AGENT_STORE": "memory",
        "BEDROCK_REGION": "",
        "AWS_BEARER_TOKEN_BEDROCK": "",
        "HOMEFRONT_EMAIL_FROM": "",
        "HOMEFRONT_SES_REGION": "",
        "HOMEFRONT_LIVE_DATAGOV": "0",
        "HOMEFRONT_AUTOSTART_AGENT": "0",  # never start a real agent process
    }
)
os.environ.pop("AWS_PROFILE", None)

import jwt  # noqa: E402
import pytest  # noqa: E402

from tests.helpers import (  # noqa: E402
    APP_CLIENT_ID,
    DEMO_STAFFERS,
    JWKS,
    JWKS_URL,
    PRODUCTION_ROLE_STAFFERS,
    USER_POOL_ID,
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "production_roles: use the real Agents-tab role resolver on a moto mandate store "
        "(no legacy dict resolver); see the `production_roles` fixture",
    )


@pytest.fixture
def cognito_login(monkeypatch):
    """Point our Cognito check (app.core.cognito) at the test pool in tests/helpers.py:
    tokens from `helpers.auth()` verify, nothing reaches AWS."""
    from app.core import cognito
    from app.core.config import get_settings

    def fake_fetch_data(self):
        assert self.uri == JWKS_URL, self.uri
        return JWKS

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch_data)
    monkeypatch.setenv("COGNITO_USER_POOL_ID", USER_POOL_ID)
    monkeypatch.setenv("COGNITO_STAFF_CLIENT_ID", APP_CLIENT_ID)
    get_settings.cache_clear()
    cognito._jwks_client.cache_clear()
    yield
    get_settings.cache_clear()
    cognito._jwks_client.cache_clear()


@pytest.fixture
def old_tables():
    """moto DynamoDB with the teammates' tables (agents, audit, approvals), for the
    tests that exercise their DynamoDB repositories."""
    from moto import mock_aws

    from app.core import dynamodb

    with mock_aws():
        dynamodb.dynamodb_resource.cache_clear()  # a resource made outside this mock
        dynamodb.create_tables()
        yield
    dynamodb.dynamodb_resource.cache_clear()


class _NoAws:
    """Stand-in AWS client: every call fails as if there were no credentials."""

    def __getattr__(self, name):
        def fail(*_args, **_kwargs):
            from botocore.exceptions import NoCredentialsError

            raise NoCredentialsError()

        return fail


@pytest.fixture(autouse=True)
def _no_real_email(monkeypatch):
    """No test can reach real SES/IAM/STS. Tests that need them patch
    handlers._ses_client / email_admin._aws_client themselves."""
    from app.agent_iam import ensure_package_paths

    ensure_package_paths()
    from identity_gateway import email_admin
    from mcp_tools import handlers

    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: _NoAws())
    monkeypatch.setattr(email_admin, "_aws_client", lambda service: _NoAws())
    monkeypatch.setattr(email_admin, "_sleep", lambda _s: None)
    email_admin.clear_cache()
    yield
    email_admin.clear_cache()


@pytest.fixture(autouse=True)
def _no_connection_gate(monkeypatch):
    """Same rule as packages/identity-gateway/tests/conftest.py: tests here run without Lane I's
    connection gate unless they install one (test_connect_prompt.py and test_draft_edit.py install a
    double through `gateway.connections`). The gate itself is tested in
    packages/identity-gateway/tests/test_gw_needs_connection.py and test_gw_connections_merged.py."""
    from app.agent_iam import ensure_package_paths

    ensure_package_paths()
    import identity_gateway.gateway as gw

    monkeypatch.setattr(gw, "connections", None, raising=False)


# --- Agents-tab role resolver (docs/loops/agent-permissions.md, Design 6) ----------------------


@pytest.fixture(autouse=True)
def _legacy_role_resolver(request):
    """LEGACY TESTS ONLY. The Agents tab now caps every create, grant and tool call by the caller's
    office role, read from the STAFFER item in the mandate store. The older tests here never set up
    that store, so they get a dict-backed resolver instead: a user named in docs/ROLES.md has their
    ROLES.md role, and any other caller is a chief_of_staff (so the old tests keep exercising what they
    were written for, not the role caps).

    It does NOT apply to tests marked `production_roles` (test_agent_permissions_api.py and
    test_privilege_escalation.py): those use the production resolver against moto, so a missing
    wiring in Backend/app/agent_iam.py cannot hide behind this fixture.

    It is a no-op until `agents_api.set_role_resolver` exists, so the suite stays collectable while
    the feature is built; the production_roles tests fail in the meantime."""
    if request.node.get_closest_marker("production_roles"):
        yield
        return
    from app.agent_iam import ensure_package_paths

    ensure_package_paths()
    from identity_gateway import agents_api
    from mandate_contracts.ddb import StafferItem

    install = getattr(agents_api, "set_role_resolver", None)
    if install is None:
        yield
        return

    def resolve(user_id):
        group, role = DEMO_STAFFERS.get(user_id, ("admin", "chief_of_staff"))
        return StafferItem(active=True, office="rep-x", role=group, office_role=role)

    install(resolve)
    yield


@pytest.fixture
def production_roles(monkeypatch):
    """The real wiring, no stubs: a moto mandate store holding STAFFER items (helpers.PRODUCTION_ROLE_STAFFERS)
    set as the process gateway, then `agent_iam.include_agents_api` run again so the Agents tab's role
    resolver is the one production installs. Yields the MandateStore: tests change a role or offboard
    someone with `store.put_staffer` / `store.set_staffer_active`, exactly as an admin would in DynamoDB."""
    import secrets

    import boto3
    from fastapi import FastAPI
    from moto import mock_aws

    from app.agent_iam import ensure_package_paths, include_agents_api

    ensure_package_paths()
    from identity_gateway import gateway as gateway_module
    from identity_gateway.authz import StarterPolicyAuthorizer
    from identity_gateway.gateway import Gateway, set_gateway
    from identity_gateway.storage import MandateStore, create_table
    from mandate_contracts.ddb import StafferItem

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    secret = secrets.token_hex(24)
    monkeypatch.setenv("MANDATE_SIGNING_SECRET", secret)
    previous = gateway_module._gateway
    with mock_aws():
        store = MandateStore(create_table(boto3.resource("dynamodb", region_name="us-east-1")))
        for username, (group, office_role, office, active) in PRODUCTION_ROLE_STAFFERS.items():
            store.put_staffer(
                username, StafferItem(active=active, office=office, role=group, office_role=office_role)
            )
        set_gateway(Gateway(store, StarterPolicyAuthorizer(), secret))
        include_agents_api(FastAPI())  # production wiring (actor + role resolver), on a throwaway app
        yield store
    set_gateway(previous)
