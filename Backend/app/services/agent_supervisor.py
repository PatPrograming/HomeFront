"""Starts the chat agent for a session by itself, so the staffer never needs a terminal.

On with HOMEFRONT_AUTOSTART_AGENT=1; off by default and in tests. When a staffer's message
arrives for a session with no running agent, the backend starts one child process:

    agent/.venv/bin/python agent/agent.py --sid <sid> --chat     (cwd = repo root)

The child authenticates itself exactly as when started by hand: its own Cognito client secret
from agent/.env, then the broker, then a mandate token on every gateway call. The backend never
reads that secret or sees a mandate token, and the child gets only an allowlist of environment
variables (AWS credentials, region, PATH), never the token-signing secret.

One process per session. It is stopped when the session is revoked, the staffer is offboarded,
the session expires, or the server shuts down. A session gets at most `max_starts` starts, so
an agent that keeps crashing is not restarted forever. Start and stop are logged, not stored.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

log = logging.getLogger("homefront.agent_supervisor")

REPO = Path(__file__).resolve().parents[3]
AGENT_PYTHON = REPO / "agent" / ".venv" / "bin" / "python"
AGENT_ENV_FILE = REPO / "agent" / ".env"
MAX_STARTS_PER_SESSION = 3
SWEEP_SECONDS = 15
STOP_GRACE_SECONDS = 5

# What the child inherits from the server. Everything else (MANDATE_SIGNING_SECRET, the api.data.gov
# key, Cognito and AVP settings) stays in the server: the agent reads its own agent/.env.
CHILD_ENV_KEYS = (
    "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",  # python.org Python on macOS needs these for HTTPS to Cognito
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_REGION", "AWS_DEFAULT_REGION",
    # Container credentials (App Runner / ECS instance role): how boto3 finds the role in the cloud.
    "AWS_CONTAINER_CREDENTIALS_FULL_URI", "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN", "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    "MANDATE_GATEWAY_URL", "MANDATE_API_URL",
)


class AgentStartError(Exception):
    """The agent could not be started; the message says why, for the staffer."""


class Proc(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...


@dataclass
class _Running:
    proc: Proc
    staffer_id: str
    ends_at: float  # the session's end, epoch seconds


def agent_command(sid: str) -> tuple[list[str], Path]:
    return [str(AGENT_PYTHON), "agent/agent.py", "--sid", sid, "--chat"], REPO


def child_env(parent: dict[str, str]) -> dict[str, str]:
    return {key: parent[key] for key in CHILD_ENV_KEYS if key in parent}


def launch(sid: str) -> Proc:
    """Start the real agent process. Its output goes to the server's terminal."""
    if not AGENT_PYTHON.exists():
        raise AgentStartError("agent/.venv is not set up on the server")
    if not AGENT_ENV_FILE.exists():
        raise AgentStartError("agent/.env is missing on the server, so the agent has no Cognito identity")
    argv, cwd = agent_command(sid)
    return subprocess.Popen(argv, cwd=cwd, env=child_env(dict(os.environ)))


class AgentSupervisor:
    def __init__(self, launcher: Callable[[str], Proc] | None = None, max_starts: int = MAX_STARTS_PER_SESSION):
        self._launch = launcher or launch
        self._max_starts = max_starts
        self._running: dict[str, _Running] = {}
        self._starts: dict[str, int] = {}
        self._lock = threading.Lock()

    def ensure_running(self, sid: str, staffer_id: str, ends_at: float) -> Literal["started", "running"]:
        """Start the session's agent unless it is already running. Raises AgentStartError."""
        with self._lock:
            current = self._running.get(sid)
            if current and current.proc.poll() is None:
                return "running"
            if current:
                log.info("agent exited sid=%s pid=%s code=%s", sid, current.proc.pid, current.proc.poll())
                del self._running[sid]
            if time.time() >= ends_at:
                raise AgentStartError("this session has expired; start a new conversation")
            starts = self._starts.get(sid, 0)
            if starts >= self._max_starts:
                raise AgentStartError(f"it stopped {starts} times in this session, so it is not restarted again")
            try:
                proc = self._launch(sid)
            except OSError as error:  # e.g. the agent's python is missing
                raise AgentStartError(str(error)) from error
            self._starts[sid] = starts + 1
            self._running[sid] = _Running(proc, staffer_id, ends_at)
            log.info("agent started sid=%s pid=%s for=%s (start %d of %d)",
                     sid, proc.pid, staffer_id, starts + 1, self._max_starts)
            return "started"

    def stop(self, sid: str, reason: str) -> None:
        with self._lock:
            running = self._running.pop(sid, None)
        if running is None:
            return
        if running.proc.poll() is None:
            running.proc.terminate()
            try:
                running.proc.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                running.proc.kill()
        log.info("agent stopped sid=%s pid=%s reason=%s", sid, running.proc.pid, reason)

    def stop_staffer(self, staffer_id: str, reason: str) -> None:
        with self._lock:
            sids = [sid for sid, r in self._running.items() if r.staffer_id == staffer_id]
        for sid in sids:
            self.stop(sid, reason)

    def stop_all(self, reason: str) -> None:
        with self._lock:
            sids = list(self._running)
        for sid in sids:
            self.stop(sid, reason)

    def sweep(self, is_live: Callable[[str], bool]) -> None:
        """Stop agents whose session ended, was revoked, or whose staffer is no longer active."""
        now = time.time()
        with self._lock:
            running = list(self._running.items())
        for sid, r in running:
            if now >= r.ends_at:
                self.stop(sid, "session expired")
            elif not is_live(sid):
                self.stop(sid, "session revoked, expired or staffer inactive")


# --- on AWS: one Fargate task per session ---------------------------------------------


class EcsAgentSupervisor:
    """Same job as AgentSupervisor when the API runs on AWS Lambda (deploy/lambda).

    A Lambda process is short-lived and there are many of them, so the agent can't be a child
    process: each session's agent is an ECS Fargate task (the `agent` image, which signs in with
    its own Cognito secret from its task definition). Which task runs for which session, and how
    many times it was started, is kept in the runtime table (`agent#<sid>`), so every Lambda
    process sees the same answer. Starting is claimed with a conditional write, so two messages
    arriving together start one task.
    """

    CLAIM_SECONDS = 60  # a start claimed this recently is treated as running

    def __init__(self, table, ecs, *, cluster: str, task_definition: str, subnets: list[str],
                 security_groups: list[str], gateway_url: Callable[[], str],
                 container: str = "agent", max_starts: int = MAX_STARTS_PER_SESSION):
        self.table, self.ecs = table, ecs
        self.cluster, self.task_definition, self.container = cluster, task_definition, container
        self.subnets, self.security_groups = subnets, security_groups
        self.gateway_url = gateway_url
        self._max_starts = max_starts

    @staticmethod
    def _key(sid: str) -> dict[str, str]:
        return {"id": f"agent#{sid}"}

    def _task_is_live(self, task_arn: str) -> bool:
        tasks = self.ecs.describe_tasks(cluster=self.cluster, tasks=[task_arn]).get("tasks", [])
        return bool(tasks) and tasks[0].get("lastStatus") != "STOPPED" and tasks[0].get("desiredStatus") != "STOPPED"

    def ensure_running(self, sid: str, staffer_id: str, ends_at: float) -> Literal["started", "running"]:
        from botocore.exceptions import ClientError

        item = self.table.get_item(Key=self._key(sid), ConsistentRead=True).get("Item") or {}
        task_arn = item.get("task_arn") or ""
        if task_arn and self._task_is_live(task_arn):
            return "running"
        if not task_arn and item and time.time() - float(item.get("claimed_at", 0)) < self.CLAIM_SECONDS:
            return "running"  # another request is starting it right now
        if task_arn:
            log.info("agent exited sid=%s task=%s", sid, task_arn.rsplit("/", 1)[-1])
        if time.time() >= ends_at:
            raise AgentStartError("this session has expired; start a new conversation")
        starts = int(item.get("starts", 0))
        if starts >= self._max_starts:
            raise AgentStartError(f"it stopped {starts} times in this session, so it is not restarted again")

        claim = {"id": f"agent#{sid}", "kind": "agent", "staffer_id": staffer_id, "ends_at": int(ends_at),
                 "starts": starts + 1, "task_arn": "", "claimed_at": int(time.time()), "expires": int(ends_at) + 3600}
        try:
            self.table.put_item(
                Item=claim,
                ConditionExpression="attribute_not_exists(id) OR (starts = :old AND task_arn = :arn)",
                ExpressionAttributeValues={":old": starts, ":arn": task_arn},
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return "running"  # another request claimed this start
            raise

        try:
            response = self.ecs.run_task(
                cluster=self.cluster,
                taskDefinition=self.task_definition,
                launchType="FARGATE",
                count=1,
                startedBy=f"homefront-{sid}"[:36],
                networkConfiguration={"awsvpcConfiguration": {
                    "subnets": self.subnets, "securityGroups": self.security_groups, "assignPublicIp": "ENABLED"}},
                overrides={"containerOverrides": [{
                    "name": self.container,
                    "command": ["--sid", sid, "--chat"],
                    "environment": [{"name": "MANDATE_GATEWAY_URL", "value": self.gateway_url()}],
                }]},
            )
        except Exception as error:
            raise AgentStartError(f"the agent task could not be started ({type(error).__name__})") from error
        tasks = response.get("tasks") or []
        if not tasks:
            reason = (response.get("failures") or [{}])[0].get("reason", "no task started")
            raise AgentStartError(f"the agent task could not be started ({reason})")
        task_arn = tasks[0]["taskArn"]
        self.table.update_item(Key=self._key(sid), UpdateExpression="SET task_arn = :arn",
                               ExpressionAttributeValues={":arn": task_arn})
        log.info("agent started sid=%s task=%s for=%s (start %d of %d)",
                 sid, task_arn.rsplit("/", 1)[-1], staffer_id, starts + 1, self._max_starts)
        return "started"

    def stop(self, sid: str, reason: str) -> None:
        item = self.table.get_item(Key=self._key(sid), ConsistentRead=True).get("Item") or {}
        task_arn = item.get("task_arn")
        if not task_arn:
            return
        self.ecs.stop_task(cluster=self.cluster, task=task_arn, reason=reason[:255])
        self.table.update_item(Key=self._key(sid), UpdateExpression="SET task_arn = :none",
                               ExpressionAttributeValues={":none": ""})
        log.info("agent stopped sid=%s task=%s reason=%s", sid, task_arn.rsplit("/", 1)[-1], reason)

    def _running_items(self) -> list[dict]:
        from boto3.dynamodb.conditions import Attr

        items: list[dict] = []
        kwargs: dict = {"FilterExpression": Attr("kind").eq("agent") & Attr("task_arn").gt("")}
        while True:
            page = self.table.scan(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def stop_staffer(self, staffer_id: str, reason: str) -> None:
        for item in self._running_items():
            if item.get("staffer_id") == staffer_id:
                self.stop(item["id"].removeprefix("agent#"), reason)

    def stop_all(self, reason: str) -> None:
        """Lambda processes end all the time; that must not stop anyone's agent."""

    def sweep(self, is_live: Callable[[str], bool]) -> None:
        now = time.time()
        for item in self._running_items():
            sid = item["id"].removeprefix("agent#")
            if now >= float(item.get("ends_at", 0)):
                self.stop(sid, "session expired")
            elif not is_live(sid):
                self.stop(sid, "session revoked, expired or staffer inactive")


def ecs_from_env() -> EcsAgentSupervisor:
    import boto3

    from app.core.dynamodb import dynamodb_resource

    def split(name: str) -> list[str]:
        return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]

    return EcsAgentSupervisor(
        dynamodb_resource().Table(os.environ["HOMEFRONT_RUNTIME_TABLE"]),
        boto3.client("ecs"),
        cluster=os.environ["AGENT_ECS_CLUSTER"],
        task_definition=os.environ["AGENT_ECS_TASK_DEFINITION"],
        subnets=split("AGENT_ECS_SUBNETS"),
        security_groups=split("AGENT_ECS_SECURITY_GROUPS"),
        # Set at cold start by app.lambda_handler (the function URL, known only once deployed).
        gateway_url=lambda: os.environ["PUBLIC_URL"].rstrip("/") + "/mcp",
    )


# --- the server's one supervisor -----------------------------------------------------

_supervisor: AgentSupervisor | EcsAgentSupervisor | None = None
_configured = False


def from_env() -> AgentSupervisor | EcsAgentSupervisor | None:
    flag = os.environ.get("HOMEFRONT_AUTOSTART_AGENT", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return None
    if os.environ.get("HOMEFRONT_AGENT_LAUNCHER", "").strip().lower() == "ecs":
        return ecs_from_env()
    return AgentSupervisor()


def get_supervisor() -> AgentSupervisor | None:
    """None means auto-start is off: the staffer (or a developer) starts the agent by hand."""
    global _supervisor, _configured
    if not _configured:
        _supervisor, _configured = from_env(), True
    return _supervisor


def set_supervisor(supervisor: AgentSupervisor | None) -> None:
    global _supervisor, _configured
    _supervisor, _configured = supervisor, True


def _session_is_live(sid: str) -> bool:
    """The broker's rule: an active session, an active staffer, and not past the session's end."""
    from identity_gateway.gateway import get_gateway

    from app.routers.sessions import SESSION_TTL_SECONDS, _created_epoch

    store = get_gateway().store
    session = store.get_session(sid)
    if session is None or session.status != "active":
        return False
    staffer = store.get_staffer(session.acting_for)
    not_expired = time.time() < _created_epoch(session.created_at) + SESSION_TTL_SECONDS
    return staffer is not None and staffer.active and not_expired


def sweep_once() -> None:
    supervisor = get_supervisor()
    if supervisor:
        supervisor.sweep(_session_is_live)


class _Stderr(logging.StreamHandler):
    """Writes to whatever sys.stderr is now, so a redirected stderr still gets the lines."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, _):
        pass


def log_to_terminal() -> None:
    """Show the start and stop lines in the gateway's terminal.

    Nothing else in the server configures logging (uvicorn only sets up its own loggers), so
    without this the INFO lines would be dropped. Safe to call more than once.
    """
    log.setLevel(logging.INFO)
    if not any(isinstance(h, _Stderr) for h in log.handlers):
        handler = _Stderr()
        handler.setFormatter(logging.Formatter("%(asctime)s homefront: %(message)s"))
        log.addHandler(handler)


@contextlib.asynccontextmanager
async def supervision(sweep_seconds: float = SWEEP_SECONDS):
    """For the app's lifespan: sweep while the server runs, stop every agent when it shuts down."""

    async def sweeper():
        while True:
            await asyncio.sleep(sweep_seconds)
            try:
                await asyncio.to_thread(sweep_once)
            except Exception:  # a DynamoDB hiccup must not end the sweeping
                log.exception("agent sweep failed")

    log_to_terminal()
    task = asyncio.create_task(sweeper())
    try:
        yield
    finally:
        task.cancel()
        supervisor = get_supervisor()
        if supervisor:
            supervisor.stop_all("server shutdown")
