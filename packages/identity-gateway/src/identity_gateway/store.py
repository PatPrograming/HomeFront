from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import Any, Callable, Protocol

# Agent ids appear in URLs; keep them short and URL-safe. Mirrors the
# check constraint on public.agents.id.
AGENT_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{2,63}$"

DEFAULT_MAX_STEPS = 10

# Fields a caller may change through update_agent(). Grants and token_version
# go through the grant methods so token invalidation stays consistent.
EDITABLE_FIELDS = frozenset({"name", "agency", "instructions", "model", "max_steps", "active"})


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
    token_version: int = 1
    active: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class TokenRecord:
    token: str
    agent_id: str
    token_version: int
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
    return replace(agent, tool_grants=set(agent.tool_grants))


class MemoryAgentRepository:
    """Process-local agent storage. Default for demos and tests; lost on restart."""

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
        instructions=(
            "You handle public communications for the agency. Research with data.gov and "
            "public web pages, then draft and send clear, accurate emails to residents."
        ),
    )


class GatewayStore:
    """Agents (via a pluggable repository), plus in-memory workload tokens and
    the ATLAS-tagged audit log."""

    def __init__(self, agents: AgentRepository | None = None) -> None:
        self._lock = Lock()
        self._agents: AgentRepository = agents or MemoryAgentRepository()
        self.tokens: dict[str, TokenRecord] = {}
        self.audit: list[dict[str, Any]] = []

    def use_agent_repository(self, repo: AgentRepository) -> None:
        with self._lock:
            self._agents = repo

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

    def issue_token(self, agent_id: str, ttl_seconds: int = 3600) -> TokenRecord:
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
        with self._lock:
            event = {
                **event,
                "id": f"aud_{uuid.uuid4().hex[:10]}",
                "ts": time.time(),
            }
            self.audit.insert(0, event)
            self.audit = self.audit[:500]
            return event

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.audit[:limit])
