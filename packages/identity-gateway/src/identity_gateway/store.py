from __future__ import annotations

import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import Any, Callable, Iterable, Protocol

from .approvals import ApprovalRepository, MemoryApprovalRepository
from .audit import AuditRepository, MemoryAuditRepository, compact_atlas

log = logging.getLogger(__name__)

# Agent ids appear in URLs; keep them short and URL-safe. Mirrors the
# check constraint on public.agents.id.
AGENT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}$"

DEFAULT_MAX_STEPS = 10

# Fields a caller may change through update_agent(). Grants and token_version
# go through the grant methods so token invalidation stays consistent.
EDITABLE_FIELDS = frozenset({"name", "agency", "instructions", "model", "max_steps", "active"})

# Per-agent mailing list: the only addresses email.send may reach, and the
# audience of email.send_list.
MAX_RECIPIENTS = 500
_EMAIL_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"'.][^@\s,;<>\"']*$")


class InvalidRecipientsError(ValueError):
    def __init__(self, invalid: list[str], message: str | None = None) -> None:
        self.invalid = invalid
        super().__init__(message or f"Invalid email addresses: {', '.join(invalid)}")


def normalize_email(address: str) -> str:
    return address.strip().lower()


def normalize_recipients(addresses: Iterable[str]) -> list[str]:
    """Trimmed, lower-cased, de-duplicated and sorted. Raises InvalidRecipientsError."""
    cleaned = {normalize_email(a) for a in addresses if a and a.strip()}
    invalid = sorted(a for a in cleaned if len(a) > 254 or not _EMAIL_RE.match(a))
    if invalid:
        raise InvalidRecipientsError(invalid)
    if len(cleaned) > MAX_RECIPIENTS:
        raise InvalidRecipientsError([], f"A mailing list holds at most {MAX_RECIPIENTS} addresses.")
    return sorted(cleaned)


@dataclass
class AgentRecord:
    agent_id: str
    name: str
    agency: str
    tool_grants: set[str]
    # The agent's purpose; the runner uses it as the system prompt.
    instructions: str = ""
    # None = use the runner's default model.
    model: str | None = None
    max_steps: int = DEFAULT_MAX_STEPS
    # Cognito user id (`sub`). None = system/demo agent.
    owner_id: str | None = None
    # Agent type (see agent_types.py); sets the tool ceiling. Fixed at creation.
    kind: str = "custom"
    # Mailing list (normalized, see normalize_recipients).
    recipients: list[str] = field(default_factory=list)
    token_version: int = 1
    active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class TokenRecord:
    token: str
    agent_id: str
    token_version: int
    # Groups this token's calls in the activity log (the run id for agent runs).
    session_id: str = field(default_factory=lambda: f"ses_{uuid.uuid4().hex[:12]}")
    issued_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 3600)


class DuplicateAgentError(Exception):
    """An agent with this id already exists."""


class AgentRepository(Protocol):
    """Persistence for agent definitions. Implementations return copies:
    mutating a returned record has no effect until it is passed to save()."""

    def list(self) -> list[AgentRecord]: ...

    def get(self, agent_id: str) -> AgentRecord | None: ...

    def insert(self, agent: AgentRecord) -> AgentRecord:
        """Raises DuplicateAgentError if the id is taken."""
        ...

    def save(self, agent: AgentRecord) -> AgentRecord: ...

    def count(self) -> int: ...


def _copy(agent: AgentRecord) -> AgentRecord:
    return replace(agent, tool_grants=set(agent.tool_grants), recipients=list(agent.recipients))


class MemoryAgentRepository:
    """Process-local agent storage. Default for demos and tests; lost on restart."""

    backend = "memory"

    def __init__(self, seed: bool = True) -> None:
        self._agents: dict[str, AgentRecord] = {}
        if seed:
            demo = demo_agent()
            self._agents[demo.agent_id] = demo

    def list(self) -> list[AgentRecord]:
        return [_copy(a) for a in self._agents.values()]

    def get(self, agent_id: str) -> AgentRecord | None:
        agent = self._agents.get(agent_id)
        return _copy(agent) if agent else None

    def insert(self, agent: AgentRecord) -> AgentRecord:
        if agent.agent_id in self._agents:
            raise DuplicateAgentError(agent.agent_id)
        self._agents[agent.agent_id] = _copy(agent)
        return _copy(agent)

    def save(self, agent: AgentRecord) -> AgentRecord:
        if agent.agent_id not in self._agents:
            raise KeyError(agent.agent_id)
        self._agents[agent.agent_id] = _copy(agent)
        return _copy(agent)

    def count(self) -> int:
        return len(self._agents)


def demo_agent() -> AgentRecord:
    # Seeded into DynamoDB at startup by identity_gateway.dynamo_repo.seed_demo_agent.
    return AgentRecord(
        agent_id="agent-comms-01",
        name="Agency Comms Agent",
        agency="Example Civic Agency",
        tool_grants={"email.send", "web.scrape", "datagov.search"},
        recipients=["constituent@example.gov"],
        instructions=(
            "You handle public communications for the agency. Research with data.gov and "
            "public web pages, then draft and send clear, accurate emails to residents."
        ),
    )


class GatewayStore:
    """Agents (via a pluggable repository), plus in-memory workload tokens and
    the ATLAS-tagged audit log."""

    def __init__(
        self, agents: AgentRepository | None = None, audit: AuditRepository | None = None
    ) -> None:
        self._lock = Lock()
        self._agents: AgentRepository = agents or MemoryAgentRepository()
        self._audit: AuditRepository = audit or MemoryAuditRepository()
        # Pending/decided human approvals for requires_hitl tools.
        self.approvals: ApprovalRepository = MemoryApprovalRepository()
        # Events the durable audit store rejected; still listed so nothing vanishes.
        self._unsaved_audit = MemoryAuditRepository()
        self.audit_write_errors = 0
        self.last_audit_error: str | None = None
        # Set by the host app when a configured store couldn't be used.
        self.storage_warnings: list[str] = []
        self.tokens: dict[str, TokenRecord] = {}

    def use_agent_repository(self, repo: AgentRepository) -> None:
        with self._lock:
            self._agents = repo

    def use_audit_repository(self, repo: AuditRepository) -> None:
        with self._lock:
            self._audit = repo

    def use_approval_repository(self, repo: ApprovalRepository) -> None:
        with self._lock:
            self.approvals = repo

    @property
    def approval_backend(self) -> str:
        return getattr(self.approvals, "backend", type(self.approvals).__name__)

    @property
    def agent_backend(self) -> str:
        return getattr(self._agents, "backend", type(self._agents).__name__)

    @property
    def audit_backend(self) -> str:
        return getattr(self._audit, "backend", type(self._audit).__name__)

    # --- agents -----------------------------------------------------------

    def agent_count(self) -> int:
        return self._agents.count()

    def list_agents(self) -> list[AgentRecord]:
        return self._agents.list()

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        return self._agents.get(agent_id)

    def create_agent(
        self,
        name: str,
        agency: str,
        tool_grants: list[str],
        agent_id: str | None = None,
        *,
        instructions: str = "",
        model: str | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        owner_id: str | None = None,
        kind: str = "custom",
        recipients: Iterable[str] = (),
    ) -> AgentRecord:
        rec = AgentRecord(
            agent_id=agent_id or f"agent-{uuid.uuid4().hex[:8]}",
            name=name,
            agency=agency,
            tool_grants=set(tool_grants),
            instructions=instructions,
            model=model,
            max_steps=max_steps,
            owner_id=owner_id,
            kind=kind,
            recipients=normalize_recipients(recipients),
        )
        with self._lock:
            return self._agents.insert(rec)

    def update_agent(self, agent_id: str, **changes: Any) -> AgentRecord:
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Not editable: {sorted(unknown)}")

        def apply(agent: AgentRecord) -> None:
            for key, value in changes.items():
                setattr(agent, key, value)

        return self._mutate(agent_id, apply)

    def set_recipients(self, agent_id: str, addresses: Iterable[str]) -> AgentRecord:
        recipients = normalize_recipients(addresses)

        def apply(agent: AgentRecord) -> None:
            agent.recipients = recipients

        return self._mutate(agent_id, apply)

    def set_grants(self, agent_id: str, grants: list[str]) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            agent.tool_grants = set(grants)
            agent.token_version += 1

        return self._mutate(agent_id, apply)

    def grant_tool(self, agent_id: str, tool: str) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            agent.tool_grants.add(tool)
            agent.token_version += 1

        return self._mutate(agent_id, apply)

    def revoke_tool(self, agent_id: str, tool: str) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            # Keep token_version stable so the mid-task Bearer token still
            # authenticates; subsequent calls to this tool hit 403 AML.T0053.
            agent.tool_grants.discard(tool)

        return self._mutate(agent_id, apply)

    def revoke_all(self, agent_id: str) -> AgentRecord:
        def apply(agent: AgentRecord) -> None:
            agent.tool_grants.clear()
            agent.token_version += 1

        return self._mutate(agent_id, apply)

    def _mutate(self, agent_id: str, apply: Callable[[AgentRecord], None]) -> AgentRecord:
        # The lock makes read-modify-write atomic within this process. It does
        # not guard against a second gateway process writing the same row.
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise KeyError(agent_id)
            apply(agent)
            return self._agents.save(agent)

    # --- workload tokens --------------------------------------------------

    def issue_token(
        self, agent_id: str, ttl_seconds: int = 3600, session_id: str | None = None
    ) -> TokenRecord:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise KeyError(agent_id)
        if not agent.active:
            raise ValueError("Agent is inactive")
        rec = TokenRecord(
            token=f"gr_{secrets.token_urlsafe(24)}",
            agent_id=agent_id,
            token_version=agent.token_version,
            expires_at=time.time() + ttl_seconds,
        )
        if session_id:
            rec.session_id = session_id
        with self._lock:
            self.tokens[rec.token] = rec
        return rec

    def resolve_token(self, token: str) -> tuple[AgentRecord, TokenRecord] | None:
        with self._lock:
            tok = self.tokens.get(token)
        if not tok or tok.expires_at < time.time():
            return None
        # Read the agent fresh on every call so a revoke (from any process,
        # when backed by DynamoDB) applies to the very next tool call.
        agent = self._agents.get(tok.agent_id)
        if not agent or not agent.active:
            return None
        if tok.token_version != agent.token_version:
            # Mid-task revoke / grant change invalidated this token version.
            return None
        return agent, tok

    # --- audit ------------------------------------------------------------

    def append_audit(self, event: dict[str, Any]) -> dict[str, Any]:
        event = {
            **event,
            "id": f"aud_{uuid.uuid4().hex[:10]}",
            "ts": time.time(),
        }
        for key in ("atlas", "untrusted_input_atlas"):
            if key in event:
                event[key] = compact_atlas(event[key])
        try:
            self._audit.append(event)
        except Exception as exc:  # never let logging break a decision
            # Error code only (e.g. ExpiredTokenException): AWS messages can carry ARNs.
            response = getattr(exc, "response", None)
            code = (response.get("Error") or {}).get("Code") if isinstance(response, dict) else None
            reason = code or type(exc).__name__
            with self._lock:
                self.audit_write_errors += 1
                self.last_audit_error = reason
            log.warning("audit write to %s failed (%s); kept in memory", self.audit_backend, reason)
            self._unsaved_audit.append(event)
        return event

    def list_audit(
        self,
        agent_ids: Iterable[str],
        limit: int = 100,
        *,
        trace_id: str | None = None,
        decision: str | None = None,
        type_: str | None = None,
    ) -> list[dict[str, Any]]:
        agent_ids = list(agent_ids)
        filters = {"trace_id": trace_id, "decision": decision, "type_": type_}
        events = self._audit.list(agent_ids, limit, **filters)
        unsaved = self._unsaved_audit.list(agent_ids, limit, **filters)
        if unsaved:
            events = sorted(events + unsaved, key=lambda e: e.get("ts", 0), reverse=True)
        return events[:limit]
