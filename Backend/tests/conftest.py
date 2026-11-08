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
    }
)
os.environ.pop("AWS_PROFILE", None)

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
