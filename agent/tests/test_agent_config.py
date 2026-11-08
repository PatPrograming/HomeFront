"""Agent wiring: env defaults, token sources and the broker call (check C7). No network."""

import base64
import json

import httpx
import pytest

import agent as cli
from mandate_agent.broker import BrokerError, fetch_mandate_token
from mandate_agent.config import Settings


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("MANDATE_GATEWAY_URL", "MANDATE_API_URL", "BEDROCK_MODEL_ID", "AWS_REGION", "MANDATE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("COGNITO_DOMAIN", "mandate-test.auth.us-east-1.amazoncognito.com")
    monkeypatch.setenv("COGNITO_AGENT_CLIENT_ID", "agent-client")
    monkeypatch.setenv("COGNITO_AGENT_CLIENT_SECRET", "agent-secret")


def test_defaults(clean_env):
    s = Settings.from_env()
    assert s == Settings("http://127.0.0.1:8080/mcp", "http://127.0.0.1:8080", "us.anthropic.claude-sonnet-5", "us-east-1")


def test_env_overrides(clean_env, monkeypatch):
    monkeypatch.setenv("MANDATE_GATEWAY_URL", "http://127.0.0.1:8000/mcp")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    s = Settings.from_env()
    assert s.gateway_url == "http://127.0.0.1:8000/mcp" and s.api_url == "http://127.0.0.1:8000"
    assert s.model_id.startswith("us.anthropic.claude-haiku")


def broker_transport(calls, cognito_status=200, broker_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/oauth2/token":
            return httpx.Response(cognito_status, json={"access_token": "agent-cc-token"})
        return httpx.Response(broker_status, json={"mandateToken": "mandate-xyz", "expiresAt": 0})

    return httpx.MockTransport(handler)


def test_broker_uses_client_credentials_then_requests_a_mandate(clean_env):
    calls = []
    token = fetch_mandate_token("http://127.0.0.1:8080", "sid-123", http=httpx.Client(transport=broker_transport(calls)))
    assert token == "mandate-xyz"
    cognito, broker = calls
    assert str(cognito.url) == "https://mandate-test.auth.us-east-1.amazoncognito.com/oauth2/token"
    assert cognito.headers["authorization"] == "Basic " + base64.b64encode(b"agent-client:agent-secret").decode()
    assert b"grant_type=client_credentials" in cognito.content and b"scope=mandate%2Ftools" in cognito.content
    assert str(broker.url) == "http://127.0.0.1:8080/api/v1/sessions/sid-123/mandate"
    assert broker.headers["authorization"] == "Bearer agent-cc-token" and json.loads(broker.content) == {}


@pytest.mark.parametrize("cognito_status,broker_status", [(401, 200), (200, 403), (200, 404)])
def test_broker_refusal_raises(clean_env, cognito_status, broker_status):
    http = httpx.Client(transport=broker_transport([], cognito_status, broker_status))
    with pytest.raises(BrokerError):
        fetch_mandate_token("http://127.0.0.1:8080", "sid-123", http=http)


def test_token_order_flag_then_env_then_sid(clean_env, monkeypatch):
    settings = Settings.from_env()
    monkeypatch.setattr(cli, "fetch_mandate_token", lambda api, sid: f"broker:{sid}")
    assert cli.resolve_token(cli.parse_args(["--token", "t1", "--sid", "s"]), settings) == "t1"
    monkeypatch.setenv("MANDATE_TOKEN", "t2")
    assert cli.resolve_token(cli.parse_args(["--sid", "s"]), settings) == "t2"
    monkeypatch.delenv("MANDATE_TOKEN")
    assert cli.resolve_token(cli.parse_args(["--sid", "s"]), settings) == "broker:s"


def test_no_token_source_exits_with_help(clean_env):
    with pytest.raises(SystemExit, match="--sid"):
        cli.resolve_token(cli.parse_args([]), Settings.from_env())


def test_broker_refusal_exits_3_without_starting(clean_env, monkeypatch, capsys):
    def refuse(api, sid):
        raise BrokerError("The broker refused a mandate for session s (HTTP 403).")

    monkeypatch.setattr(cli, "fetch_mandate_token", refuse)
    assert cli.main(["--sid", "s"]) == 3
    assert "Cannot start" in capsys.readouterr().out


def test_prompt_lists_the_emails():
    assert cli.build_prompt(["email-1", "email-3"]) == "Work today's inbox. The new emails are: email-1, email-3. Handle each one."


def test_cognito_domain_as_full_url(clean_env, monkeypatch):
    monkeypatch.setenv("COGNITO_DOMAIN", "https://mandate-test.auth.us-east-1.amazoncognito.com/")
    calls = []
    fetch_mandate_token("http://127.0.0.1:8080", "sid-1", http=httpx.Client(transport=broker_transport(calls)))
    assert str(calls[0].url) == "https://mandate-test.auth.us-east-1.amazoncognito.com/oauth2/token"


def test_env_file_fills_missing_values_but_never_overrides_the_shell(clean_env, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nGUARDRAIL_ID=from-file\nBEDROCK_MODEL_ID=from-file\n\nEMPTY=\n")
    monkeypatch.delenv("GUARDRAIL_ID", raising=False)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "from-shell")
    cli.load_env_file(env_file)
    import os
    assert os.environ["GUARDRAIL_ID"] == "from-file"
    assert os.environ["BEDROCK_MODEL_ID"] == "from-shell"
    assert "EMPTY" not in os.environ
    monkeypatch.delenv("GUARDRAIL_ID")


def test_missing_env_file_is_fine(tmp_path):
    cli.load_env_file(tmp_path / "absent.env")
