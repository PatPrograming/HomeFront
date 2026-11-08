"""Chat mode: answer the staffer's messages in the HomeFront console until the session ends.

The agent reads and posts the chat with its mandate token (`/api/v1/agent/messages`), so it can
only see the chat of the session it was issued for. Offboarding the staffer closes the chat and
the gateway at the same moment: the next poll gets 403 and the agent stops.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from .broker import BrokerError
from .config import Settings
from .runner import EXIT_STOPPED, run_chat_turn

POLL_SECONDS = 1.0
TOKEN_REFRESH_SECONDS = 10 * 60  # mandate tokens last 15 minutes; get a new one well before that

# Exit codes the console's supervisor reads (3, EXIT_STOPPED, means the session is over).
EXIT_NOT_SET_UP = 4  # no usable agent identity: a Cognito setting is missing
EXIT_UNREACHABLE = 5  # Cognito or the broker could not be reached, before any reply was possible
EXIT_FAILED = 6  # a turn failed and the agent said why in the chat
EXIT_IDENTITY_REFUSED = 7  # Cognito refused the agent's client id or secret
EXIT_UNEXPLAINED = 8  # it failed and could not post why (no token yet, or the post failed): the server says it

# The staffer's words for each cause. Never the exception's own text: it can carry keys or tracebacks.
CREDENTIALS_EXPIRED = (
    "I stopped: the server's AWS sign-in has expired, so I can't reach the AI model. "
    "Ask your admin to refresh the server's AWS credentials, then send your message again."
)
MODEL_ACCESS_DENIED = "I stopped: this server isn't allowed to use the AI model (Bedrock access denied). Ask your admin."
MODEL_BUSY = "I stopped: the AI model is busy right now. Wait a minute, then send your message again."
GATEWAY_DOWN = (
    "I stopped: I can't reach the HomeFront gateway. Ask your admin to check that it's running, "
    "then send your message again."
)
UNEXPECTED = "I stopped because of an unexpected problem on the server. Send your message again, or ask your admin."
EXPIRED_CODES = {"ExpiredToken", "ExpiredTokenException", "UnrecognizedClientException", "InvalidClientTokenId",
                 "RequestExpired", "InvalidSignatureException"}
BUSY_CODES = {"ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException", "TooManyRequestsException"}


class ChatClosed(RuntimeError):
    """The API refused the agent's token for the chat: the session is over."""


class ChatChannel:
    """The agent's side of the console chat."""

    def __init__(self, api_url: str, http: httpx.Client | None = None):
        self.url = f"{api_url}/api/v1/agent/messages"
        self.http = http or httpx.Client(timeout=10)

    def _check(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code in (401, 403):
            raise ChatClosed(f"the console refused the agent's token (HTTP {response.status_code})")
        response.raise_for_status()
        return response.json()

    def fetch(self, token: str, after: str | None) -> tuple[list[dict[str, Any]], str | None]:
        """Messages after the cursor, oldest first, and the cursor for the next fetch."""
        params = {"after": after} if after else {}
        page = self._check(self.http.get(self.url, params=params, headers={"Authorization": f"Bearer {token}"}))
        return page["messages"], page["next"]

    def post(self, token: str, text: str, detail: str | None = None) -> None:
        body = {"text": text, **({"detail": detail} if detail else {})}
        self._check(self.http.post(self.url, json=body, headers={"Authorization": f"Bearer {token}"}))


def _chain(error: BaseException):
    """The error and everything it was raised from (model libraries wrap the AWS error)."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def explain(error: BaseException) -> tuple[str, str]:
    """(what the staffer reads, a short technical detail for admins). Neither includes the error's text."""
    for e in _chain(error):
        name = type(e).__name__
        code = getattr(e, "response", None)
        code = code.get("Error", {}).get("Code", "") if isinstance(code, dict) else ""
        if name in {"NoCredentialsError", "PartialCredentialsError", "CredentialRetrievalError"} or code in EXPIRED_CODES:
            return CREDENTIALS_EXPIRED, f"{name}: {code}" if code else name
        if code == "AccessDeniedException":
            return MODEL_ACCESS_DENIED, f"{name}: {code}"
        if code in BUSY_CODES or "Throttl" in name:
            return MODEL_BUSY, f"{name}: {code}" if code else name
        if isinstance(e, (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError)) or name in {
            "EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError"}:
            return GATEWAY_DOWN, name
        if name == "MCPClientInitializationError":  # what the MCP client raises when the gateway is down
            return GATEWAY_DOWN, name
        if isinstance(e, httpx.HTTPStatusError) and e.response.status_code >= 500:
            return GATEWAY_DOWN, f"{name}: HTTP {e.response.status_code}"
    return UNEXPECTED, type(error).__name__


def _broker_failure(error: BrokerError) -> tuple[int, str]:
    """Exit code and terminal line for a broker failure: the real cause, not always "revoked"."""
    if error.kind == "credentials_refused":
        return EXIT_IDENTITY_REFUSED, f"Cannot start: {error}"
    if error.kind == "not_set_up":
        return EXIT_NOT_SET_UP, (
            f"Cannot start: the agent has no usable identity. {error} "
            "(needs COGNITO_DOMAIN, COGNITO_AGENT_CLIENT_ID and COGNITO_AGENT_CLIENT_SECRET in agent/.env)"
        )
    if error.kind == "unreachable":
        return EXIT_UNREACHABLE, f"Cannot reach Cognito or the broker: {error}"
    return EXIT_STOPPED, f"Stopping: {error} The session was revoked, ended or expired."


def unanswered(messages: list[dict[str, Any]]) -> list[str]:
    """The staffer's messages after the agent's last reply, so a restarted agent picks up where it left off."""
    last_reply = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
    return [m["text"] for m in messages[last_reply + 1 :] if m["role"] == "user"]


def serve_chat(
    channel: ChatChannel,
    get_token: Callable[[], str],
    settings: Settings,
    inbox: list[str],
    model: Any = None,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Answer chat messages until the session is revoked. Returns the exit code.

    Every failure ends with a reason: in the chat when the agent can still post (staff words, plus a
    short technical detail only admins see), and always in the terminal. EXIT_* codes above tell the
    console's supervisor which message to post when the agent couldn't.
    """
    history: list[Any] = []
    token: str | None = None
    try:
        token, minted = get_token(), time.monotonic()
        messages, cursor = channel.fetch(token, None)
        pending = unanswered(messages)
        out(f"Chat ready: answering {settings.api_url}'s console for this session. Ctrl-C to quit.")
        while True:
            for text in pending:
                out(f"[chat] staffer: {text[:100]}")
                reply, guard, history = run_chat_turn(text, token, settings, inbox, history, model=model, out=out)
                channel.post(token, reply)
                if guard.stop_reason:
                    out(guard.report())
                    return EXIT_STOPPED
            sleep(POLL_SECONDS)
            if time.monotonic() - minted > TOKEN_REFRESH_SECONDS:
                token, minted = get_token(), time.monotonic()
            messages, cursor = channel.fetch(token, cursor)
            pending = [m["text"] for m in messages if m["role"] == "user"]
    except ChatClosed as closed:
        out(f"Stopping: {closed}. The session was revoked, or the staffer was offboarded.")
        return EXIT_STOPPED
    except BrokerError as error:
        code, line = _broker_failure(error)
        out(line)
        return code
    except Exception as error:  # Bedrock, AWS credentials, the gateway: say so instead of dying silently
        text, detail = explain(error)
        out(f"{text} [{detail}]")
        if token is None:
            return EXIT_UNEXPLAINED
        try:
            channel.post(token, text, detail)
        except Exception as post_error:  # the gateway itself may be what failed
            out(f"Could not post that to the chat either ({type(post_error).__name__}).")
            return EXIT_UNEXPLAINED
        return EXIT_FAILED
