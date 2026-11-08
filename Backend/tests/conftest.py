import os

# Deterministic settings, independent of any developer Backend/.env (env vars
# take precedence over the .env file). Must run before the app is imported.
from tests.helpers import APP_CLIENT_ID, JWKS, JWKS_URL, REGION, USER_POOL_ID

# TEST_DYNAMODB_ENDPOINT_URL=http://localhost:8000 runs the suite against
# DynamoDB Local (scripts/run-dynamodb-local.sh) instead of moto.
_REAL_DYNAMO = os.environ.get("TEST_DYNAMODB_ENDPOINT_URL", "")

os.environ.update(
    {
        "COGNITO_USER_POOL_ID": USER_POOL_ID,
        "COGNITO_APP_CLIENT_ID": APP_CLIENT_ID,
        "AWS_REGION": REGION,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "DYNAMODB_TABLE_PREFIX": "test-",
        "DYNAMODB_ENDPOINT_URL": _REAL_DYNAMO,
        "AGENT_STORE": "memory",
        "AGENT_ADMIN_REQUIRES_LOGIN": "false",
        "BEDROCK_REGION": "",
        "AWS_BEARER_TOKEN_BEDROCK": "",
        # Never a real sender in tests: apply_aws_env() only fills unset keys,
        # so these blanks keep a developer .env's sender out.
        "HOMEFRONT_EMAIL_FROM": "",
        "HOMEFRONT_SES_REGION": "",
        "HOMEFRONT_LIVE_DATAGOV": "0",
        # Never a real X app in tests (see apply_aws_env); tests that need one set it.
        "X_CLIENT_ID": "",
        "X_CLIENT_SECRET": "",
        "X_REDIRECT_URI": "",
        "FRONTEND_URL": "",
    }
)
os.environ.pop("AWS_PROFILE", None)

import pytest  # noqa: E402

import jwt  # noqa: E402
from moto import mock_aws  # noqa: E402

# In-memory DynamoDB for the whole session (unless targeting DynamoDB Local).
_aws = None if _REAL_DYNAMO else mock_aws()
if _aws:
    _aws.start()


def _fake_fetch_data(self):
    # Serve the test pool's JWKS instead of calling Cognito, and make sure the
    # verifier asked for the right pool's URL.
    assert self.uri == JWKS_URL, self.uri
    return JWKS


jwt.PyJWKClient.fetch_data = _fake_fetch_data

from app.core.dynamodb import create_tables  # noqa: E402

create_tables()


def pytest_unconfigure(config):
    if _aws:
        _aws.stop()


class _NoAws:
    """Stand-in AWS client: every call fails as if there were no credentials."""

    def __getattr__(self, name):
        def fail(*_args, **_kwargs):
            from botocore.exceptions import NoCredentialsError

            raise NoCredentialsError()

        return fail


@pytest.fixture(autouse=True)
def _no_real_email(monkeypatch):
    """No test can reach real SES/IAM/STS, whatever .env says. Tests that need
    them patch handlers._ses_client / email_admin._aws_client themselves."""
    from mcp_tools import handlers
    from identity_gateway import email_admin

    monkeypatch.delenv("HOMEFRONT_EMAIL_FROM", raising=False)
    monkeypatch.setattr(handlers, "_ses_client", lambda quick=False: _NoAws())
    monkeypatch.setattr(email_admin, "_aws_client", lambda service: _NoAws())
    monkeypatch.setattr(email_admin, "_sleep", lambda _s: None)
    email_admin.clear_cache()
    yield
    email_admin.clear_cache()
