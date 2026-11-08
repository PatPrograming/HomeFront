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


# --- the server's one supervisor -----------------------------------------------------

_supervisor: AgentSupervisor | None = None
_configured = False


def from_env() -> AgentSupervisor | None:
    flag = os.environ.get("HOMEFRONT_AUTOSTART_AGENT", "").strip().lower()
    return AgentSupervisor() if flag in {"1", "true", "yes", "on"} else None


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
