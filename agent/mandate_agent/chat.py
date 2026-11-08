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

    def post(self, token: str, text: str) -> None:
        self._check(self.http.post(self.url, json={"text": text}, headers={"Authorization": f"Bearer {token}"}))


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
    """Answer chat messages until the session is revoked. Returns the exit code (3: stopped)."""
    history: list[Any] = []
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
    except (ChatClosed, BrokerError) as closed:
        out(f"Stopping: {closed}. The session was revoked or has ended.")
        return EXIT_STOPPED
