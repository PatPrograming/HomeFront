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
# AAuth on the Agents/Monitoring pathway needs a ≥32-char HS256 secret.
os.environ.setdefault(
    "MANDATE_SIGNING_SECRET", "test-mandate-signing-secret-32chars!!"
)
os.environ.setdefault("AAUTH_SIGNING_KEY", os.environ["MANDATE_SIGNING_SECRET"])
os.environ.setdefault("HOMEFRONT_AUTH_PROTOCOLS", "legacy,aauth,idjag")
os.environ.pop("AWS_PROFILE", None)

import jwt  # noqa: E402
import pytest  # noqa: E402

from tests.helpers import APP_CLIENT_ID, JWKS, JWKS_URL, USER_POOL_ID  # noqa: E402


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
