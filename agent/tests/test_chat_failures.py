"""Every way the chat agent can fail reaches the staffer in plain words, never silently.

No network, no AWS, no Bedrock: a fake channel, fake token sources and fake failures.
"""

from __future__ import annotations

import httpx
import pytest
from botocore.exceptions import ClientError, NoCredentialsError
from conftest import TOKEN
from fake_model import ScriptedModel
from mandate_agent import chat as chat_mod
from mandate_agent.broker import BrokerError, fetch_agent_client_token, fetch_mandate_token
from mandate_agent.chat import (
    EXIT_FAILED,
    EXIT_IDENTITY_REFUSED,
    EXIT_NOT_SET_UP,
    EXIT_UNREACHABLE,
    ChatClosed,
    explain,
    serve_chat,
)
from mandate_agent.config import Settings
from mandate_agent.runner import EXIT_STOPPED

SETTINGS = Settings("http://127.0.0.1:1/mcp", "http://127.0.0.1:1", "scripted", "us-east-1")


class Channel:
    def __init__(self, pages):
        self.pages = list(pages)
        self.posted: list[tuple[str, str | None]] = []

    def fetch(self, token, after):
        if not self.pages:
            raise ChatClosed("the console refused the agent's token (HTTP 403)")
        return self.pages.pop(0), "cursor"

    def post(self, token, text, detail=None):
        self.posted.append((text, detail))


def msg(text):
    return {"id": text, "ts": "t", "role": "user", "text": text}


def run(channel, get_token, out=None, turn=None, monkeypatch=None):
    lines: list[str] = []
    if turn is not None:
        monkeypatch.setattr(chat_mod, "run_chat_turn", turn)
    code = serve_chat(channel, get_token, SETTINGS, ["email-1"], model=ScriptedModel(["ok"]),
                      out=lines.append, sleep=lambda _: None)
    return code, lines


# --- 1. the agent secret ------------------------------------------------------------------


@pytest.fixture
def cognito_env(monkeypatch):
    monkeypatch.setenv("COGNITO_DOMAIN", "mandate-test.auth.us-east-1.amazoncognito.com")
    monkeypatch.setenv("COGNITO_AGENT_CLIENT_ID", "agent-client")
    monkeypatch.setenv("COGNITO_AGENT_CLIENT_SECRET", "agent-secret")


@pytest.mark.parametrize("name", ["COGNITO_AGENT_CLIENT_SECRET", "COGNITO_AGENT_CLIENT_ID", "COGNITO_DOMAIN"])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_agent_setting_is_a_clear_broker_error_not_a_keyerror(cognito_env, monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name)
    else:
        monkeypatch.setenv(name, value)
    with pytest.raises(BrokerError) as raised:
        fetch_agent_client_token(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert raised.value.kind == "not_set_up"
    assert name in str(raised.value) and "agent/.env" in str(raised.value)


def test_cognito_refusing_the_secret_is_its_own_kind(cognito_env):
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    with pytest.raises(BrokerError) as raised:
        fetch_agent_client_token(http)
    assert raised.value.kind == "credentials_refused"


def test_an_unreachable_cognito_or_broker_is_its_own_kind(cognito_env):
    def down(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(BrokerError) as raised:
        fetch_mandate_token("http://127.0.0.1:1", "sid-1", http=httpx.Client(transport=httpx.MockTransport(down)))
    assert raised.value.kind == "unreachable"


def test_the_broker_refusing_the_session_is_its_own_kind(cognito_env):
    def handler(request):
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "cc"})
        return httpx.Response(403, json={"detail": "Session revoked or staffer inactive"})

    with pytest.raises(BrokerError) as raised:
        fetch_mandate_token("http://127.0.0.1:1", "sid-1", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert raised.value.kind == "session_refused" and raised.value.status == 403


# --- 5. the real cause, not "revoked" ------------------------------------------------------


def _refuse(kind):
    def get_token():
        raise BrokerError("nope", kind=kind)

    return get_token


def test_a_missing_secret_says_so_and_exits_with_its_own_code():
    code, lines = run(Channel([]), _refuse("not_set_up"))
    assert code == EXIT_NOT_SET_UP
    text = "\n".join(lines)
    assert "agent/.env" in text and "COGNITO_AGENT_CLIENT_SECRET" in text
    assert "revoked" not in text


def test_a_refused_secret_or_unreachable_broker_is_not_called_revoked():
    for kind, expected in (("credentials_refused", EXIT_IDENTITY_REFUSED), ("unreachable", EXIT_UNREACHABLE)):
        code, lines = run(Channel([]), _refuse(kind))
        assert code == expected and "revoked" not in "\n".join(lines), kind


def test_a_revoked_session_still_says_revoked():
    code, lines = run(Channel([]), _refuse("session_refused"))
    assert code == EXIT_STOPPED and "revoked" in "\n".join(lines)


def test_the_console_refusing_the_token_says_the_session_ended():
    code, lines = run(Channel([]), lambda: TOKEN)
    assert code == EXIT_STOPPED and "revoked" in "\n".join(lines)


# --- 2. failures during a turn reach the chat ------------------------------------------------


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "secret-looking AKIA1234 detail"}}, "Converse")


@pytest.mark.parametrize(
    ("error", "staff_words", "detail"),
    [
        (NoCredentialsError(), "AWS sign-in has expired", "NoCredentialsError"),
        (_client_error("ExpiredTokenException"), "AWS sign-in has expired", "ClientError: ExpiredTokenException"),
        (_client_error("AccessDeniedException"), "isn't allowed to use the AI model", "ClientError: AccessDeniedException"),
        (_client_error("ThrottlingException"), "busy right now", "ClientError: ThrottlingException"),
        (httpx.ConnectError("refused"), "can't reach the HomeFront gateway", "ConnectError"),
        (httpx.HTTPStatusError("boom", request=httpx.Request("GET", "http://x"), response=httpx.Response(502)),
         "can't reach the HomeFront gateway", "HTTPStatusError: HTTP 502"),
        (RuntimeError("Traceback (most recent call last): password=hunter2"), "unexpected problem", "RuntimeError"),
    ],
)
def test_a_failed_turn_posts_plain_words_then_exits(monkeypatch, error, staff_words, detail):
    def turn(*args, **kwargs):
        raise error

    channel = Channel([[msg("check my email")]])
    code, _ = run(channel, lambda: TOKEN, turn=turn, monkeypatch=monkeypatch)

    assert code == EXIT_FAILED
    ((text, posted_detail),) = channel.posted
    assert staff_words in text
    assert "send your message again" in text.lower() or "ask your admin" in text.lower()
    assert posted_detail == detail
    for leak in ("AKIA1234", "hunter2", "Traceback", "password"):
        assert leak not in text and leak not in posted_detail


def test_a_failure_wrapped_by_the_model_library_is_still_recognised():
    try:
        try:
            raise _client_error("ThrottlingException")
        except ClientError as inner:
            raise RuntimeError("model call failed") from inner
    except RuntimeError as outer:
        text, detail = explain(outer)
    assert "busy right now" in text and detail == "ClientError: ThrottlingException"


def test_if_even_posting_fails_the_agent_still_exits_cleanly(monkeypatch):
    class DeadChannel(Channel):
        def post(self, token, text, detail=None):
            raise httpx.ConnectError("gateway down")

    def turn(*args, **kwargs):
        raise httpx.ConnectError("gateway down")

    code, lines = run(DeadChannel([[msg("hi")]]), lambda: TOKEN, turn=turn, monkeypatch=monkeypatch)
    assert code == EXIT_FAILED and any("can't reach the HomeFront gateway" in line for line in lines)
