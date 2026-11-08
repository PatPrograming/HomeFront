"""Mount the HomeFront gateway onto the HomeFront API.

The gateway lives in `packages/identity-gateway`. This module puts its packages
on the import path and attaches it to the host app:

- `/api/v1/tools`, `/api/v1/atlas/*`, `/api/v1/demo/matrix`: public catalogs.
- `/api/v1/mcp/call`: dashboard tool calls, authenticated by a mandate token.
- `/mcp`: the real MCP endpoint for the agent, authenticated by a mandate token.

There is no route that issues a mandate token on its own: mandate tokens come
only from the broker (`routers/sessions.py`), which needs a staffer login and
the agent's own client credentials.

It also mounts the teammates' agent-management API (`identity_gateway.agents_api`:
agents, grants, email setup, runs, approvals, activity log) behind our Cognito
login, whatever `AGENT_ADMIN_REQUIRES_LOGIN` says: GET routes need
`require_staff`, every other method (create, edit, grant, mint a workload token,
SES admin, test email, approve, start a run) needs `require_admin`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.routing import APIRoute

from app.core.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_SRC = [
    _REPO_ROOT / "packages" / "mandate-contracts" / "src",
    _REPO_ROOT / "packages" / "atlas-enricher" / "src",
    _REPO_ROOT / "packages" / "mcp-tools" / "src",
    _REPO_ROOT / "packages" / "identity-gateway" / "src",
]


def ensure_package_paths() -> None:
    for path in _PACKAGE_SRC:
        resolved = str(path)
        if path.is_dir() and resolved not in sys.path:
            sys.path.insert(0, resolved)


def include_agent_iam(host_app: FastAPI, prefix: str = "") -> None:
    """Attach the gateway routes under `prefix` and the MCP endpoint at `/mcp`."""
    ensure_package_paths()
    from identity_gateway.app import include_gateway

    include_gateway(host_app, prefix=prefix)
    include_agents_api(host_app, prefix=prefix)


def actor_from_login(authorization: str | None):
    """The teammates' API's caller, from our Cognito staff login (401/403 otherwise)."""
    from app.core.cognito import STAFF_GROUPS, verify_staff_token
    from identity_gateway.agents_api import Actor

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication failed")
    identity = verify_staff_token(token.strip())
    if not identity.groups & STAFF_GROUPS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a staff member")
    return Actor(user_id=identity.username)


def include_agents_api(host_app: FastAPI, prefix: str = "") -> None:
    """Mount the teammates' agent-management routes: reads for staff, writes for admins."""
    from app.core.cognito import require_admin, require_staff
    from identity_gateway import agents_api

    agents_api.set_actor_resolver(actor_from_login)
    configure_bedrock_runner()
    if get_settings().agent_store == "dynamodb":
        use_dynamodb_stores(agents_api.store)

    reads, writes = APIRouter(), APIRouter()
    for route in agents_api.router.routes:
        if not isinstance(route, APIRoute):
            continue
        (reads if route.methods <= {"GET", "HEAD"} else writes).routes.append(route)
    host_app.include_router(reads, prefix=prefix, dependencies=[Depends(require_staff)])
    host_app.include_router(writes, prefix=prefix, dependencies=[Depends(require_admin)])


def _aws_reason(exc: Exception) -> str:
    """Short, ARN-free reason for a storage warning shown by GET /status."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return (response.get("Error") or {}).get("Code") or type(exc).__name__
    return type(exc).__name__


def use_dynamodb_stores(store) -> None:
    """Switch agents and the activity log to DynamoDB. Each falls back to memory
    on its own if its table can't be reached; GET /status reports which."""
    import logging

    from boto3.dynamodb.conditions import Key
    from botocore.exceptions import BotoCoreError, ClientError

    from identity_gateway.approvals import DynamoApprovalRepository
    from identity_gateway.audit import DynamoAuditRepository
    from identity_gateway.dynamo_repo import DynamoAgentRepository, seed_demo_agent
    from identity_gateway.reports import DynamoReportRepository

    from app.core.dynamodb import table, table_name

    log = logging.getLogger(__name__)
    hint = "Fix AWS credentials, run ./scripts/run-dynamodb-local.sh, or set AGENT_STORE=memory."

    try:
        repo = DynamoAgentRepository(table("agents"))
        seed_demo_agent(repo)  # replaces the SQL migration's seed row
        store.use_agent_repository(repo)
    except (ClientError, BotoCoreError) as exc:
        msg = f"Agents are in memory only: {table_name('agents')} unavailable ({_aws_reason(exc)})"
        store.storage_warnings.append(msg)
        log.warning("%s; full error: %s. %s", msg, exc, hint)

    try:
        audit_table = table("audit")
        # Probe with Query (in BackendDataPolicy) so a missing table fails now,
        # not on the first event.
        audit_table.query(KeyConditionExpression=Key("agent_id").eq("_probe"), Limit=1)
        store.use_audit_repository(DynamoAuditRepository(audit_table))
    except (ClientError, BotoCoreError) as exc:
        msg = f"Activity log is in memory only: {table_name('audit')} unavailable ({_aws_reason(exc)})"
        store.storage_warnings.append(msg)
        log.warning("%s; full error: %s. %s", msg, exc, hint)

    try:
        approvals_table = table("approvals")
        approvals_table.get_item(Key={"id": "_probe"})  # GetItem is in BackendDataPolicy
        store.use_approval_repository(DynamoApprovalRepository(approvals_table))
    except (ClientError, BotoCoreError) as exc:
        msg = f"Approvals are in memory only: {table_name('approvals')} unavailable ({_aws_reason(exc)})"
        store.storage_warnings.append(msg)
        log.warning("%s; full error: %s. %s", msg, exc, hint)

    try:
        reports_table = table("reports")
        reports_table.query(KeyConditionExpression=Key("agent_id").eq("_probe"), Limit=1)
        store.use_report_repository(DynamoReportRepository(reports_table))
    except (ClientError, BotoCoreError) as exc:
        msg = f"Reports are in memory only: {table_name('reports')} unavailable ({_aws_reason(exc)})"
        store.storage_warnings.append(msg)
        log.warning("%s; full error: %s. %s", msg, exc, hint)


def configure_bedrock_runner() -> None:
    """Attach the Claude-on-Bedrock runner if a region is configured."""
    from identity_gateway import agents_api as gateway
    from identity_gateway.runner import AgentRunner, RunnerConfig

    settings = get_settings()
    region = settings.resolved_bedrock_region
    if not region:
        gateway.configure_runner(None)
        return

    def make_client():
        from anthropic import AnthropicBedrockMantle, BetaRefusalFallbackMiddleware

        middleware = []
        if settings.bedrock_fallback_model_id:
            # Bedrock has no server-side `fallbacks`; the SDK middleware retries
            # a refused request on the fallback model client-side.
            middleware.append(
                BetaRefusalFallbackMiddleware([{"model": settings.bedrock_fallback_model_id}])
            )
        return AnthropicBedrockMantle(
            aws_region=region,
            api_key=settings.aws_bearer_token_bedrock or None,
            aws_profile=settings.aws_profile or None,
            middleware=middleware,
        )

    def fallback_state():
        from anthropic import BetaFallbackState

        return BetaFallbackState()

    gateway.configure_runner(
        AgentRunner(
            client_factory=make_client,
            call_tool=gateway.gateway_tool_call,
            issue_token=gateway._issue_run_token,
            config=RunnerConfig(
                default_model=settings.bedrock_model_id,
                effort=settings.runner_effort,
            ),
            fallback_state_factory=fallback_state,
        )
    )
