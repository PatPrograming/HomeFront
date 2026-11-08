"""Run HomeFront's AI staffer on the office inbox.

Real flow (Maya pressed "Start assistant" and the console showed a session id):
    python agent/agent.py --sid <sid>
        gets the agent's Cognito client_credentials token, asks lane B's broker for a
        15-minute mandate token for that session, then works the inbox.

Chat flow (Maya talks to the agent in the console instead of it working the inbox once):
    python agent/agent.py --sid <sid> --chat
        same identity steps, then answers the console chat until the session is revoked,
        getting a fresh mandate token from the broker every 10 minutes.

Dev flow (before the broker exists, or against agent/stub_server.py):
    MANDATE_TOKEN=$(MANDATE_DEV_SECRET=... python -m mandate_contracts.dev) python agent/agent.py
    python agent/agent.py --token <token> --gateway http://127.0.0.1:8000/mcp

Exit code 0: finished. Exit code 3: stopped because access was revoked or the token was rejected.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "mandate-contracts" / "src"))
sys.path.insert(0, str(REPO / "agent"))

from mandate_agent.broker import BrokerError, fetch_mandate_token  # noqa: E402
from mandate_agent.config import Settings  # noqa: E402

DEFAULT_EMAILS = "email-1,email-2,email-3"
# The chat demo's inbox: four case-status emails (enough approvals to earn autonomy), personal
# data, the disguised staff memo and a newsletter.
CHAT_EMAILS = "email-1,email-7,email-13,email-19,email-2,email-3,email-12"
ENV_FILE = REPO / "agent" / ".env"  # git-ignored; see agent/.env.example


def load_env_file(path: Path) -> None:
    """Fill unset variables from KEY=VALUE lines. Values already in the shell win; empty values are skipped."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and value.strip():
            os.environ.setdefault(key.strip(), value.strip())


def build_prompt(email_ids: list[str]) -> str:
    return f"Work today's inbox. The new emails are: {', '.join(email_ids)}. Handle each one."


def token_source(args: argparse.Namespace, settings: Settings) -> Callable[[], str]:
    """--token, then the broker for --sid, then MANDATE_TOKEN. Calling the result gets a token.

    An explicit --sid beats a MANDATE_TOKEN left in the shell or agent/.env: otherwise the agent
    would quietly run under the dev session, and offboarding Maya would not stop it.
    """
    if args.token:
        return lambda: args.token
    if args.sid:
        return lambda: fetch_mandate_token(settings.api_url, args.sid)
    if os.environ.get("MANDATE_TOKEN"):
        return lambda: os.environ["MANDATE_TOKEN"]
    raise SystemExit("Give --sid <session id> (real flow) or --token / MANDATE_TOKEN (dev flow).")


def resolve_token(args: argparse.Namespace, settings: Settings) -> str:
    return token_source(args, settings)()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HomeFront AI staffer")
    parser.add_argument("--sid", help="session id shown to the staffer; the broker issues the mandate token")
    parser.add_argument("--token", help="mandate token for dev runs (or set MANDATE_TOKEN)")
    parser.add_argument("--gateway", help="MCP endpoint (default: MANDATE_GATEWAY_URL or http://127.0.0.1:8080/mcp)")
    parser.add_argument(
        "--emails", help=f"comma-separated email ids (default {DEFAULT_EMAILS}; with --chat {CHAT_EMAILS})"
    )
    parser.add_argument("--chat", action="store_true", help="answer the console chat until the session is revoked")
    parser.add_argument("--prompt", help="override the task prompt")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_file(ENV_FILE)
    settings = Settings.from_env(args.gateway)
    if args.chat:
        return chat(args, settings)
    try:
        token = resolve_token(args, settings)
    except BrokerError as error:
        print(f"Cannot start: {error}")
        return 3

    from mandate_agent.runner import run_inbox  # imported late so --help works without Strands

    print(f"HomeFront agent -> {settings.gateway_url} (model {settings.model_id}, {settings.region})")
    prompt = args.prompt or build_prompt((args.emails or DEFAULT_EMAILS).split(","))
    code, _ = run_inbox(prompt, token, settings)
    return code


def chat(args: argparse.Namespace, settings: Settings) -> int:
    from mandate_agent.chat import ChatChannel, serve_chat  # imported late so --help works without Strands

    print(f"HomeFront agent (chat) -> {settings.gateway_url} (model {settings.model_id}, {settings.region})")
    inbox = (args.emails or CHAT_EMAILS).split(",")
    return serve_chat(ChatChannel(settings.api_url), token_source(args, settings), settings, inbox)


if __name__ == "__main__":
    raise SystemExit(main())
