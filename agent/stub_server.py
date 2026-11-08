"""Stand-in for lane B's gateway: a FastMCP server on http://127.0.0.1:8000/mcp.

It exposes the same tools as `mandate_contracts.TOOLS` and copies the gateway's
behaviour closely enough for the agent to be built and tested before the real
gateway exists:

- No `Authorization: Bearer <token>` header (or a bad one) -> HTTP 401.
- Allow/deny with simple rules that mirror the starter Cedar policies.
- `StubState.role` (default none) limits the tools to that office role's list, like the role policies.
- A denied call returns an MCP error result with the text
  `DENIED (<reason>, ATLAS <id>)`, the same shape the gateway uses.
- `inbox_read` serves agent/data/emails.json (email-3 carries the demo
  injection) and `web_scrape` returns a page with a planted injection.
- Revocation switch: `POST /admin/revoke` (or `--revoke-after N` calls) makes
  every later call DENIED with "session revoked / delegator inactive", or HTTP
  401 with `?mode=401`.

This is a stub. It enforces nothing real; Cedar in the gateway is the guarantee.

Run:  python agent/stub_server.py [--port 8000] [--revoke-after N]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "packages" / "mandate-contracts" / "src"))

import jwt  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.server.fastmcp.tools import Tool  # noqa: E402
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from mandate_contracts import TOOLS, input_schema  # noqa: E402
from mandate_contracts.dev import verify_dev_token  # noqa: E402
from mandate_contracts.roles import ROLE_TOOLS, role_policy_id, role_reason  # noqa: E402
from pydantic import ConfigDict  # noqa: E402

DATA = HERE / "data"
OFFICE = "rep-x"  # the dev token's office; the stub has one staffer, Maya
REVOKED = "session revoked / delegator inactive"

# The page `web_scrape` returns. Its HTML comment is the planted injection.
SCRAPED_PAGE = (DATA / "scrape_page.txt").read_text().strip()


@dataclass
class StubState:
    revoked: bool = False
    revoke_mode: str = "deny"  # "deny" (MCP DENIED result) or "401" (HTTP 401)
    revoke_after: int | None = None
    hold_outbound: bool = False  # answer allowed emails and posts like the gateway does before Maya trusts them
    role: str | None = None  # office role to enforce; None means no role limit
    calls: list[dict[str, Any]] = field(default_factory=list)  # every tool call and its decision
    auth_headers: list[str | None] = field(default_factory=list)  # header on every /mcp request


def _load(name: str) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in json.loads((DATA / name).read_text())}


def _text(text: str, *, error: bool = False) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=error)


def _denied(reason: str, atlas: str, policy: str = "") -> CallToolResult:
    """Like the gateway, name the Cedar policy when one decided."""
    return _text(f"DENIED ({reason}, ATLAS {atlas})" + (f" policy: {policy}" if policy else ""), error=True)


class _AnyArgs(ArgModelBase):
    """Accept whatever arguments arrive (some contract inputs use `for` and `in`, which are not Python names)."""

    model_config = ConfigDict(extra="allow")

    def model_dump_one_level(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


# Read-only tools the stub answers with a canned line.
RESEARCH_TOOLS = (
    "census_query", "openfda_search", "eia_query", "nasa_search", "noaa_climate",
    "nps_parks", "bls_series", "fec_search", "epa_search", "fcc_search",
    "usaspending_search", "sam_entity_lookup",
)


def create_stub(state: StubState | None = None, secret: str | None = None):
    """Build a fresh stub. Returns (asgi_app, state). A FastMCP app can run only once."""
    state = state or StubState()
    emails, cases = _load("emails.json"), _load("cases.json")
    known = {c["constituentEmail"].lower() for c in cases.values()}

    def guarded(tool: str, args: dict[str, Any], rule) -> CallToolResult:
        """Revocation check first, then the tool's rule. Records every decision."""
        if state.revoke_after is not None and len(state.calls) >= state.revoke_after:
            state.revoked = True
        if state.revoked:
            result = _denied(REVOKED, "AML.T0012")
        elif state.role is not None and tool not in ROLE_TOOLS[state.role]:
            result = _denied(role_reason(state.role, tool), "AML.T0053", role_policy_id(tool))
        else:
            result = rule()
        state.calls.append(
            {"tool": tool, "args": args, "decision": "DENY" if result.isError else "ALLOW"}
        )
        return result

    def inbox_read(email_id: str) -> CallToolResult:
        def rule():
            email = emails.get(email_id)
            if email is None:
                return _text(f"No email with id {email_id}.", error=True)
            return _text(f"From: {email['from']}\nSubject: {email['subject']}\n\n{email['body']}")

        return guarded("inbox_read", {"email_id": email_id}, rule)

    def case_read(case_id: str) -> CallToolResult:
        def rule():
            case = cases.get(case_id)
            if case is None:
                return _text(f"No case with id {case_id}.", error=True)
            if case["office"] != OFFICE:  # office from the data, never from tool input
                return _denied("denied by policy", "AML.T0053")
            return _text(json.dumps(case))

        return guarded("case_read", {"case_id": case_id}, rule)

    def datagov_search(query: str) -> CallToolResult:
        return guarded(
            "datagov_search",
            {"query": query},
            lambda: _text(f"[stub] 2 datasets match '{query}': FEMA flood claims 2025; HUD senior housing waitlists."),
        )

    def web_scrape(url: str) -> CallToolResult:
        return guarded("web_scrape", {"url": url}, lambda: _text(f"[page {url}]\n{SCRAPED_PAGE}"))

    def held(category: str) -> CallToolResult:
        return _text(json.dumps({"status": "held_for_review", "draftId": f"draft-{len(state.calls)}", "category": category}))

    def email_send(to: str, subject: str, body: str, in_reply_to: str | None = None) -> CallToolResult:
        def rule():
            addr = to.strip().lower()
            if addr in known or addr.endswith(".gov"):
                return held("new_conversation") if state.hold_outbound else _text(f"Sent to {to}.")
            return _denied("denied by policy", "AML.T0086")

        return guarded("email_send", {"to": to, "subject": subject, "body": body}, rule)

    def social_post(account: str, text: str) -> CallToolResult:
        def rule():
            if account == "official":
                return held("social_post") if state.hold_outbound else _text("Posted on the official account.")
            return _denied("denied by policy", "AML.T0048")

        return guarded("social_post", {"account": account, "text": text}, rule)

    def records_bulk_export(query: str) -> CallToolResult:
        return guarded("records_bulk_export", {"query": query}, lambda: _denied("denied by policy", "AML.T0086", "forbid-bulk-export"))

    def stub_tool(name: str, rule) -> Tool:
        """A tool that takes the contract's arguments as they come and answers with `rule`."""

        def run(**args: Any) -> CallToolResult:
            return guarded(name, args, rule)

        spec = TOOLS[name]
        return Tool(
            fn=run, name=name, description=spec.description, parameters=input_schema(name),
            fn_metadata=FuncMetadata(arg_model=_AnyArgs), is_async=False,
        )

    handlers = [inbox_read, case_read, datagov_search, web_scrape, email_send, social_post, records_bulk_export]
    tools = []
    for fn in handlers:
        spec = TOOLS[fn.__name__]
        tool = Tool.from_function(fn, name=spec.name, description=spec.description)
        # Advertise the contract's JSON schema, so the agent sees what the gateway will show.
        tools.append(tool.model_copy(update={"parameters": input_schema(spec.name)}))
    tools += [stub_tool(name, lambda name=name: _text(f"[stub] {name} results.")) for name in RESEARCH_TOOLS]
    tools += [
        # The real gateway always holds a mailing-list email; the stub holds it too, whatever hold_outbound says.
        stub_tool("email_send_list", lambda: held("mailing_list")),
        stub_tool("social_reply", lambda: held("social_reply") if state.hold_outbound else _text("Replied.")),
        stub_tool("report_save", lambda: _text("Report saved.")),
    ]

    mcp = FastMCP("homefront-stub", tools=tools, stateless_http=True, json_response=True)

    @mcp.custom_route("/admin/revoke", methods=["POST"])
    async def revoke(request: Request) -> JSONResponse:
        state.revoked = True
        state.revoke_mode = request.query_params.get("mode", "deny")
        return JSONResponse({"revoked": True, "mode": state.revoke_mode})

    @mcp.custom_route("/admin/reset", methods=["POST"])
    async def reset(request: Request) -> JSONResponse:
        state.revoked, state.revoke_mode = False, "deny"
        state.calls.clear()
        state.auth_headers.clear()
        return JSONResponse({"revoked": False})

    @mcp.custom_route("/admin/log", methods=["GET"])
    async def log(request: Request) -> JSONResponse:
        return JSONResponse({"revoked": state.revoked, "calls": state.calls})

    app = mcp.streamable_http_app()

    def token_ok(header: str | None) -> bool:
        if not header or not header.startswith("Bearer ") or not header[7:].strip():
            return False
        if secret is None:  # no secret configured: any bearer token is accepted
            return True
        try:
            verify_dev_token(header[7:].strip(), secret)
            return True
        except jwt.PyJWTError:
            return False

    async def auth_middleware(scope, receive, send):
        """Every /mcp request needs a bearer token, like the gateway."""
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            header = dict(scope["headers"]).get(b"authorization")
            header = header.decode() if header else None
            state.auth_headers.append(header)
            if not token_ok(header) or (state.revoked and state.revoke_mode == "401"):
                await JSONResponse({"error": "invalid token"}, status_code=401)(scope, receive, send)
                return
        await app(scope, receive, send)

    return auth_middleware, state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--revoke-after", type=int, help="revoke the session after N tool calls")
    args = parser.parse_args()

    import uvicorn

    app, _ = create_stub(StubState(revoke_after=args.revoke_after), secret=os.environ.get("MANDATE_DEV_SECRET"))
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
