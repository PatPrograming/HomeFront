"""Run Mandate's AI staffer on the office inbox.

Real flow (Maya pressed "Start assistant" and the console showed a session id):
    python agent/agent.py --sid <sid>
        gets the agent's Cognito client_credentials token, asks lane B's broker for a
        15-minute mandate token for that session, then works the inbox.

Dev flow (before the broker exists, or against agent/stub_server.py):
    MANDATE_TOKEN=$(MANDATE_DEV_SECRET=... python -m mandate_contracts.dev) python agent/agent.py
    python agent/agent.py --token <token> --gateway http://127.0.0.1:8000/mcp

Exit code 0: finished. Exit code 3: stopped because access was revoked or the token was rejected.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "mandate-contracts" / "src"))
sys.path.insert(0, str(REPO / "agent"))

from mandate_agent.broker import BrokerError, fetch_mandate_token  # noqa: E402
from mandate_agent.config import Settings  # noqa: E402

DEFAULT_EMAILS = "email-1,email-2,email-3"


def build_prompt(email_ids: list[str]) -> str:
    return f"Work today's inbox. The new emails are: {', '.join(email_ids)}. Handle each one."


def resolve_token(args: argparse.Namespace, settings: Settings) -> str:
    """--token, then MANDATE_TOKEN, then the broker for --sid."""
    if args.token:
        return args.token
    if os.environ.get("MANDATE_TOKEN"):
        return os.environ["MANDATE_TOKEN"]
    if args.sid:
        return fetch_mandate_token(settings.api_url, args.sid)
    raise SystemExit("Give --sid <session id> (real flow) or --token / MANDATE_TOKEN (dev flow).")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mandate AI staffer")
    parser.add_argument("--sid", help="session id shown to the staffer; the broker issues the mandate token")
    parser.add_argument("--token", help="mandate token for dev runs (or set MANDATE_TOKEN)")
    parser.add_argument("--gateway", help="MCP endpoint (default: MANDATE_GATEWAY_URL or http://127.0.0.1:8080/mcp)")
    parser.add_argument("--emails", default=DEFAULT_EMAILS, help=f"comma-separated email ids (default {DEFAULT_EMAILS})")
    parser.add_argument("--prompt", help="override the task prompt")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env(args.gateway)
    try:
        token = resolve_token(args, settings)
    except BrokerError as error:
        print(f"Cannot start: {error}")
        return 3

    from mandate_agent.runner import run_inbox  # imported late so --help works without Strands

    print(f"Mandate agent -> {settings.gateway_url} (model {settings.model_id}, {settings.region})")
    prompt = args.prompt or build_prompt(args.emails.split(","))
    code, _ = run_inbox(prompt, token, settings)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
