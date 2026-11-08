"""Real MCP over streamable HTTP at /mcp, served by the Backend app on 127.0.0.1.

The Backend app runs under uvicorn in a background thread (so its lifespan runs), with a
moto-backed gateway injected through set_gateway(). The client is the mcp 1.x SDK client.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from identity_gateway.gateway import set_gateway
from identity_gateway.tokens import mint_mandate_token
from mandate_contracts import TOOLS, MandateClaims, input_schema
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def server(gateway):
    from app.main import app

    set_gateway(gateway)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not uv.started:
        assert thread.is_alive(), "uvicorn thread died during startup"
        assert time.time() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    uv.should_exit = True
    thread.join(timeout=10)


def _with_session(base: str, token: str, fn):
    async def run():
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(f"{base}/mcp", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    return asyncio.run(asyncio.wait_for(run(), timeout=30))


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


# ---- HTTP 401 before any MCP processing ----------------------------------


def test_missing_bearer_is_401(server):
    r = httpx.post(f"{server}/mcp", json=INIT_BODY, headers=MCP_HEADERS, follow_redirects=False)
    assert r.status_code == 401


def test_garbage_bearer_is_401(server):
    headers = {**MCP_HEADERS, "Authorization": "Bearer not-a-token"}
    r = httpx.post(f"{server}/mcp", json=INIT_BODY, headers=headers, follow_redirects=False)
    assert r.status_code == 401


def test_expired_bearer_is_401(server, make_token):
    now = int(time.time())
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {make_token(iat=now - 2000, exp=now - 1000)}"}
    r = httpx.post(f"{server}/mcp", json=INIT_BODY, headers=headers, follow_redirects=False)
    assert r.status_code == 401


def test_bearer_signed_with_another_secret_is_401(server, make_token):
    forged = make_token(signing_secret="attacker-secret-attacker-secret-0000")
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {forged}"}
    r = httpx.post(f"{server}/mcp", json=INIT_BODY, headers=headers, follow_redirects=False)
    assert r.status_code == 401


def test_non_bearer_authorization_header_is_401(server, make_token):
    headers = {**MCP_HEADERS, "Authorization": f"Basic {make_token()}"}
    r = httpx.post(f"{server}/mcp", json=INIT_BODY, headers=headers, follow_redirects=False)
    assert r.status_code == 401


# ---- tools/list and tools/call -------------------------------------------


def test_tools_list_returns_exactly_the_seven_contract_tools_with_their_schemas(server, make_token):
    result = _with_session(server, make_token(), lambda s: s.list_tools())
    tools = {t.name: t for t in result.tools}
    assert set(tools) == set(TOOLS) and len(result.tools) == len(TOOLS)
    for name, tool in tools.items():
        assert tool.inputSchema == input_schema(name), name


def test_bulk_export_over_mcp_is_denied_with_iserror_and_atlas_text(server, make_token, store, events_of):
    result = _with_session(
        server, make_token(), lambda s: s.call_tool("records_bulk_export", {"query": "everyone"})
    )
    assert result.isError is True
    assert _text(result).startswith("DENIED (denied by policy, ATLAS AML.T0053")
    last = events_of(store)[-1]
    assert (last.tool, last.decision, last.policy_ids) == (
        "records_bulk_export", "DENY", ["forbid-bulk-export"],
    )


def test_allowed_call_over_mcp_is_not_an_error_and_returns_the_case(server, make_token, world):
    result = _with_session(
        server, make_token(), lambda s: s.call_tool("case_read", {"case_id": world.case_x})
    )
    assert result.isError is False
    assert "Pothole on Main St" in _text(result)


def test_other_offices_case_over_mcp_is_denied(server, make_token, world):
    result = _with_session(
        server, make_token(), lambda s: s.call_tool("case_read", {"case_id": world.case_y})
    )
    assert result.isError is True
    assert _text(result).startswith("DENIED (denied by policy, ATLAS ")
    assert "Zoning appeal" not in _text(result)


def test_revoking_mid_task_denies_the_next_mcp_call_on_the_same_connection(
    server, make_token, store, world
):
    async def scenario(session):
        before = await session.call_tool("case_read", {"case_id": world.case_x})
        store.revoke_sessions_for("maya")
        after = await session.call_tool("case_read", {"case_id": world.case_x})
        return before, after

    before, after = _with_session(server, make_token(), scenario)
    assert before.isError is False
    assert after.isError is True
    assert _text(after).startswith("DENIED (session revoked / delegator inactive, ATLAS ")


def test_inactive_delegator_over_mcp_is_denied_and_untrusted_content_is_not_returned(
    server, secret, store, world
):
    now = int(time.time())
    token = mint_mandate_token(
        MandateClaims(sub=world.agent, act="maya", office="rep-x", sid=world.sid, iat=now, exp=now + 900),
        secret,
    )
    store.set_staffer_active("maya", False)
    result = _with_session(server, token, lambda s: s.call_tool("inbox_read", {"email_id": world.email_id}))
    assert result.isError is True
    assert world.email_body not in _text(result)


def test_mcp_is_served_at_the_root_not_under_the_rest_prefix(server, make_token):
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {make_token()}"}
    r = httpx.post(f"{server}/api/v1/mcp", json=INIT_BODY, headers=headers, follow_redirects=False)
    assert r.status_code in (404, 405)
