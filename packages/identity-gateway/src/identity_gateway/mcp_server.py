"""The real MCP endpoint (streamable HTTP) at `/mcp`.

It lists the contract tools with their exact schemas, and every call goes
through `guarded()`. A missing, forged or expired bearer token gets HTTP 401
before any MCP message is read. Accepts legacy HS256 mandate tokens and AAuth
(ID-JAG + proof-of-possession) credentials.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from mandate_contracts import TOOLS, input_schema

from .aauth_bridge import resolve_call_identity
from .gateway import get_gateway
from .tokens import InvalidToken

PATH = "/mcp"

server = Server("homefront-gateway")


def _bearer(headers: Any) -> str:
    value = headers.get("authorization") or headers.get("Authorization") or ""
    return value[len("Bearer ") :].strip() if value.startswith("Bearer ") else ""


def _header_map(raw: Any) -> dict[str, str]:
    if hasattr(raw, "items"):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(name=t.name, description=t.description, inputSchema=input_schema(t.name)) for t in TOOLS.values()]


# validate_input=False: bad arguments must reach guarded() so they become a logged DENY.
@server.call_tool(validate_input=False)
async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    req = server.request_context.request
    headers = _header_map(getattr(req, "headers", {}) or {})
    token = _bearer(headers)
    gateway = get_gateway()
    try:
        # guarded() talks to DynamoDB and AVP with blocking calls, so run it in a thread.
        outcome = await anyio.to_thread.run_sync(
            lambda: gateway.guarded(
                name,
                arguments or {},
                token,
                headers=headers,
                method=getattr(req, "method", "POST") or "POST",
                path=PATH,
                body=b"",
            )
        )
    except InvalidToken:
        return types.CallToolResult(content=[types.TextContent(type="text", text="DENIED (invalid token)")], isError=True)
    if not outcome.allowed:
        # A DENY with a result is "needs connection": the agent gets the JSON that says which account to ask for.
        text = json.dumps(outcome.result) if outcome.result else outcome.deny_text()
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=True)
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(outcome.result))], isError=False)


def _security() -> TransportSecuritySettings:
    hosts = os.environ.get("MANDATE_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*").split(",")
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts)


class McpEndpoint:
    """ASGI app: check the bearer token, then hand the request to the MCP session manager."""

    def __init__(self) -> None:
        self.manager: StreamableHTTPSessionManager | None = None

    @contextlib.asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        # A session manager can run only once, so each app start gets a new one.
        self.manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True, security_settings=_security())
        async with self.manager.run():
            yield
        self.manager = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        body = await request.body()
        sent = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        try:
            resolve_call_identity(
                bearer_token=_bearer(headers),
                secret=get_gateway().secret,
                headers=headers,
                method=request.method,
                path=PATH,
                body=body,
            )
        except InvalidToken:
            await JSONResponse({"detail": "Invalid or missing mandate token"}, status_code=401)(
                scope, replay_receive, send
            )
            return
        except Exception:  # gateway not configured (no secret, no AWS settings): fail closed
            await JSONResponse({"detail": "Gateway not configured"}, status_code=503)(scope, replay_receive, send)
            return
        if self.manager is None:
            await JSONResponse({"detail": "MCP endpoint not started"}, status_code=503)(scope, replay_receive, send)
            return

        # Replay the buffered body for the session manager.
        sent = False
        await self.manager.handle_request(scope, replay_receive, send)


endpoint = McpEndpoint()
