"""How the agent treats the gateway's answers. These rules are enforced in code, not left to the model.

- DENIED: the call is recorded as "denied, not done", the model is told plainly that the
  action did not happen, and any repeat of the same action is cancelled before it reaches
  the gateway (no retry loop).
- Held for review: an email or post the staffer has not let the agent send alone comes back as a
  draft in the staffer's review queue. It is recorded as "waiting for review, not sent", the model
  is told so, and the same action is not drafted twice.
- Revoked or 401: the run stops. "session revoked / delegator inactive" or "invalid token"
  in a denial, or HTTP 401 on any request, ends the agent loop after the current tools.
- At the end, `report()` prints what really happened from the gateway's answers, so the
  staffer never has to trust the model's own summary.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

import httpx
from strands.hooks import AfterToolCallEvent, AfterToolsEvent, BeforeToolCallEvent, HookProvider, HookRegistry

from mandate_contracts import TOOLS

STOP_REASONS = ("session revoked / delegator inactive", "invalid token")
CONNECTION_LOST = "Connection to the MCP server was closed"
CONNECTION_LOST_RESULT = f"Tool execution failed: {CONNECTION_LOST}"  # Strands' error text after a 401

UNAUTHORIZED = "the gateway rejected the mandate token (HTTP 401)"
ALLOW, DENY, REJECTED, ERROR = "allow", "deny", "rejected", "error"
HELD_STATUS = "held_for_review"  # mandate_contracts' gateway result for a draft kept for review
OUTBOUND_TOOLS = ("email_send", "social_post")


def result_text(result: dict[str, Any]) -> str:
    return " ".join(c.get("text", "") for c in result.get("content", []))


def is_held(tool: str, text: str) -> bool:
    """Only an outbound tool's own result can say "held": inbox text could contain the same words."""
    if tool not in OUTBOUND_TOOLS:
        return False
    try:
        return json.loads(text).get("status") == HELD_STATUS
    except (ValueError, AttributeError):
        return False


def read_verdict(result: dict[str, Any], unauthorized: bool) -> tuple[str, str | None]:
    """Sort one MCP tool result into ALLOW, DENY, REJECTED (401) or ERROR, plus why the run must stop.

    Only an error result can be a denial or a lost connection: a successful inbox_read or
    web_scrape returns attacker-controlled text that may contain the same words.
    """
    text = result_text(result)
    status = result.get("status")
    if status == "success":
        return ALLOW, None
    if status == "error" and (unauthorized or text == CONNECTION_LOST_RESULT):
        return REJECTED, UNAUTHORIZED
    if status == "error" and text.startswith("DENIED"):
        return DENY, (text if any(reason in text for reason in STOP_REASONS) else None)
    return ERROR, None


# What makes two calls "the same action" for the no-retry rule. Re-sending a denied email
# to the same address with new wording is still a retry. Tools not listed use the full input.
ACTION_KEY_FIELDS = {"email_send": ("to",), "social_post": ("account",), "case_read": ("case_id",)}


@dataclass
class ToolOutcome:
    tool: str
    tool_input: dict[str, Any]
    text: str

    def describe(self) -> str:
        args = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in self.tool_input.items())
        return f"{self.tool}({args})"

    def why_denied(self) -> str:
        """The call and the gateway's own reason, e.g. "... -> DENIED (denied by policy, ...) policy: forbid-bulk-export"."""
        reason = self.text.splitlines()[0] if self.text else ""
        return f"{self.describe()} -> {reason}" if reason.startswith("DENIED") else self.describe()


@dataclass
class MandateGuard(HookProvider):
    done: list[ToolOutcome] = field(default_factory=list)
    denied: list[ToolOutcome] = field(default_factory=list)
    blocked_retries: list[ToolOutcome] = field(default_factory=list)
    held: list[ToolOutcome] = field(default_factory=list)
    stop_reason: str | None = None
    unauthorized: bool = False  # set by BearerAuth when the gateway answers HTTP 401
    log: Callable[[str], None] = lambda line: None  # live one-line trace of each call, for the demo
    _denied_actions: set[str] = field(default_factory=set)
    _held_actions: set[str] = field(default_factory=set)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)
        registry.add_callback(AfterToolCallEvent, self.after_tool_call)
        registry.add_callback(AfterToolsEvent, self.after_tools)

    @staticmethod
    def action_key(tool: str, tool_input: dict[str, Any]) -> str:
        if TOOLS.get(tool) and TOOLS[tool].always_denied:
            return tool  # forbidden whatever the input
        fields = ACTION_KEY_FIELDS.get(tool)
        if fields:
            # "ARCHIVE@x.com " and "archive@x.com" are the same recipient, as the gateway sees it.
            picked = {f: str(tool_input.get(f, "")).strip().lower() for f in fields}
        else:
            picked = tool_input
        return f"{tool}:{json.dumps(picked, sort_keys=True)}"

    def before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name, tool_input = event.tool_use["name"], event.tool_use["input"]
        if self.stop_reason:
            event.cancel_tool = f"Not sent: the run is stopping ({self.stop_reason})."
        elif self.action_key(name, tool_input) in self._denied_actions:
            self.blocked_retries.append(ToolOutcome(name, tool_input, "blocked retry"))
            self.log(f"[blocked retry] {name}: already denied, not sent")
            event.cancel_tool = (
                f"Not sent: {name} was already DENIED by the gateway for this action. "
                "It will not be retried. Tell the staffer it was denied."
            )
        elif self.action_key(name, tool_input) in self._held_actions:
            event.cancel_tool = f"Not sent: a draft of this {name} is already waiting in the staffer's review queue."

    def after_tool_call(self, event: AfterToolCallEvent) -> None:
        if event.cancel_message:  # cancelled by before_tool_call; never reached the gateway
            return
        name, tool_input = event.tool_use["name"], event.tool_use["input"]
        text = result_text(event.result)
        outcome = ToolOutcome(name, tool_input, text)
        self.log(f"[gateway] {outcome.describe()} -> {text.splitlines()[0][:100] if text else event.result.get('status')}")

        verdict, stop = read_verdict(event.result, self.unauthorized)
        if stop:
            self.stop_reason = stop
        if verdict == REJECTED:
            self.denied.append(outcome)
        elif verdict == DENY:
            self.denied.append(outcome)
            self._denied_actions.add(self.action_key(name, tool_input))
            event.result = {
                **event.result,
                "content": [
                    {
                        "text": f"{text}\nThis action was NOT performed. Tell the staffer which action was "
                        "denied and why. Do not retry it and do not say it was done."
                    }
                ],
            }
        elif verdict == ALLOW and is_held(name, text):
            self.held.append(outcome)
            self._held_actions.add(self.action_key(name, tool_input))
            event.result = {
                **event.result,
                "content": [
                    {
                        "text": f"{text}\nThis was NOT sent. It is a draft waiting in the staffer's review "
                        "queue. Say it is waiting for review; do not say it was sent."
                    }
                ],
            }
        elif verdict == ALLOW:
            self.done.append(outcome)

    def after_tools(self, event: AfterToolsEvent) -> None:
        if self.stop_reason:
            event.end_turn = f"Stopping: {self.stop_reason}. My access was cut off, so I did nothing further."

    def correction(self) -> str | None:
        """One line naming every denied action, printed right after the model's own summary."""
        if not self.denied:
            return None
        return "Not done (denied by the gateway): " + "; ".join(o.why_denied() for o in self.denied)

    def held_note(self) -> str | None:
        """One line naming every draft left for the staffer, printed after the model's own answer."""
        if not self.held:
            return None
        return "Waiting for your review (not sent): " + "; ".join(o.describe() for o in self.held)

    def report(self) -> str:
        lines = ["", "--- What actually happened (from the gateway's answers, not the model) ---"]
        lines.append("Done:" if self.done else "Done: nothing")
        lines += [f"  ALLOW  {o.describe()}" for o in self.done]
        if self.held:
            lines.append("Held for the staffer's review, NOT sent:")
            lines += [f"  HELD   {o.describe()}" for o in self.held]
        lines.append("Denied, NOT done:" if self.denied else "Denied, NOT done: nothing")
        lines += [f"  DENY   {o.describe()}  ->  {o.text.splitlines()[0]}" for o in self.denied]
        if self.blocked_retries:
            lines.append(f"Retries of denied actions blocked before reaching the gateway: {len(self.blocked_retries)}")
        if self.stop_reason:
            lines.append(f"Run stopped: {self.stop_reason}")
        return "\n".join(lines)


class BearerAuth(httpx.Auth):
    """Adds `Authorization: Bearer <mandate token>` to every MCP request and watches for 401/403."""

    def __init__(self, token: str, guard: MandateGuard):
        self.token, self.guard = token, guard

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        if response.status_code in (401, 403):
            self.guard.unauthorized = True
