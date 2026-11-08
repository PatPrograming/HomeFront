"""AWS Lambda entry point (deploy/lambda).

Two kinds of event reach this function:

- HTTP requests from its function URL (the console, /api/v1, /mcp): handed to the FastAPI app.
- `{"homefront_job": "run", "run_id": "..."}`, sent by this function to itself when someone
  starts an agent run: the run executes here, in its own invocation (up to 15 minutes).
  Function-URL events are HTTP-shaped, so a web request can never look like a job.

At cold start, before the app is imported, it fills in what is only known in AWS:
the settings and keys (one Secrets Manager secret, HOMEFRONT_CONFIG_SECRET, holding a JSON
object of environment variables) and the function's own URL (the MCP host allowlist, CORS
origin, and the gateway URL the agent tasks are given). The agent's Cognito secret is not in
that config: only the agent tasks can read it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from urllib.parse import urlparse

import boto3

log = logging.getLogger("homefront.lambda")


def load_config(env: dict = os.environ, client=None) -> None:
    """Set each variable of the HOMEFRONT_CONFIG_SECRET JSON; ones Lambda already set (AWS_REGION) win."""
    arn = (env.get("HOMEFRONT_CONFIG_SECRET") or "").strip()
    if not arn:
        return
    client = client or boto3.client("secretsmanager")
    config = json.loads(client.get_secret_value(SecretId=arn)["SecretString"])
    for key, value in config.items():
        if not env.get(key):
            env[key] = str(value)


def learn_public_url(env: dict = os.environ, client=None) -> str:
    url = (env.get("PUBLIC_URL") or "").strip()
    if not url:
        client = client or boto3.client("lambda")
        url = client.get_function_url_config(FunctionName=env["AWS_LAMBDA_FUNCTION_NAME"])["FunctionUrl"]
    url = url.rstrip("/")
    env["PUBLIC_URL"] = url
    host = urlparse(url).netloc
    allowed = [h for h in (env.get("MANDATE_MCP_ALLOWED_HOSTS") or "127.0.0.1:*,localhost:*").split(",") if h]
    if host not in allowed:
        env["MANDATE_MCP_ALLOWED_HOSTS"] = ",".join([*allowed, host])
    env.setdefault("ALLOWED_ORIGINS", url)
    return url


def dispatch_run(run_id: str, client=None) -> None:
    """Queue a run: invoke this same function asynchronously with a job event."""
    client = client or boto3.client("lambda")
    client.invoke(
        FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
        InvocationType="Event",
        Payload=json.dumps({"homefront_job": "run", "run_id": run_id}).encode(),
    )


load_config()
learn_public_url()

from mangum import Mangum  # noqa: E402

from app.main import app  # noqa: E402
from identity_gateway import agents_api  # noqa: E402

agents_api.configure_run_dispatcher(dispatch_run)
_asgi = Mangum(app, lifespan="auto")


def handler(event, context):
    if isinstance(event, dict) and event.get("homefront_job") == "run":
        agents_api.execute_queued_run(str(event["run_id"]))
        return {"ok": True}
    # Mangum asks asyncio for "the current loop", which newer Pythons no longer make on demand.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    return _asgi(event, context)
