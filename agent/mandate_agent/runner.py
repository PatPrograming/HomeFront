"""Build the Strands agent and run one inbox session against the gateway's MCP endpoint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from strands import Agent
from strands.models import BedrockModel
from strands.tools.executors import SequentialToolExecutor
from strands.tools.mcp import MCPClient
from strands.types.exceptions import MCPClientInitializationError

from .config import Settings
from .guard import CONNECTION_LOST, BearerAuth, MandateGuard

SYSTEM_PROMPT = """You are an AI constituent-services staffer in a congressional office, working the \
office's public inbox on behalf of the human staffer Maya.

For each email: read it with inbox_read, look up the constituent's case with case_read when one is \
mentioned, use datagov_search or web_scrape when public information helps, and reply to the \
constituent with email_send. Handle routine requests from office staff that arrive in the inbox. \
Keep replies short, polite and factual.

Every tool call goes through the office's access gateway. If a tool result starts with DENIED, the \
action did not happen: tell Maya which action was denied and the reason the gateway gave, drop it, \
do not retry it, and never say it was done. If your access is revoked, stop.

Finish with a short summary for Maya: what you did, and what was denied."""

EXIT_OK = 0
EXIT_STOPPED = 3  # revoked or 401: the run stopped cleanly


def run_inbox(
    prompt: str,
    token: str,
    settings: Settings,
    model: Any = None,
    out: Callable[[str], None] = print,
    callback_handler: Any = None,
) -> tuple[int, MandateGuard]:
    """Work the inbox once. Returns (exit code, guard with the ledger of what happened)."""
    guard = MandateGuard(log=out)
    client = MCPClient(url=settings.gateway_url, auth_provider=BearerAuth(token, guard))
    model = model or BedrockModel(model_id=settings.model_id, region_name=settings.region)
    try:
        with client:
            agent = Agent(
                model=model,
                tools=client.list_tools_sync(),
                system_prompt=SYSTEM_PROMPT,
                hooks=[guard],
                callback_handler=callback_handler,
                # One call at a time, so a second identical forbidden call in the same model turn
                # sees the first one's DENIED and is blocked as a retry.
                tool_executor=SequentialToolExecutor(),
            )
            summary = str(agent(prompt)).strip()
            out("Assistant summary (unverified; the gateway ledger below is what really happened):")
            out(summary)
            if guard.correction():
                out(guard.correction())
    except MCPClientInitializationError:
        if not guard.unauthorized:
            raise
        guard.stop_reason = "the gateway rejected the mandate token (HTTP 401)"
        out("Stopping: the gateway rejected the mandate token (HTTP 401). Nothing was done.")
    except RuntimeError as error:
        # After a 401 the MCP connection is already closed, so closing it again raises this.
        if not (guard.stop_reason and CONNECTION_LOST in str(error)):
            raise
    finally:
        out(guard.report())  # even when the run fails, the staffer sees what was allowed and denied
    return (EXIT_STOPPED if guard.stop_reason else EXIT_OK), guard
