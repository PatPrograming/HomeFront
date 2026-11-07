from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class AgentRecord:
    agent_id: str
    name: str
    agency: str
    tool_grants: set[str]
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


class InMemoryStore:
    """Demo store for agents, grants, tokens, and ATLAS-tagged audit events."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.agents: dict[str, AgentRecord] = {}
        self.tokens: dict[str, TokenRecord] = {}
        self.audit: list[dict[str, Any]] = []
        self._seed()

    def _seed(self) -> None:
        demo = AgentRecord(
            agent_id="agent-comms-01",
            name="Agency Comms Agent",
            agency="Example Civic Agency",
            tool_grants={"email.send", "web.scrape", "datagov.search"},
        )
        self.agents[demo.agent_id] = demo

    def list_agents(self) -> list[AgentRecord]:
        with self._lock:
            return list(self.agents.values())

    def get_agent(self, agent_id: str) -> AgentRecord | None:
        with self._lock:
            return self.agents.get(agent_id)

    def create_agent(
        self,
        name: str,
        agency: str,
        tool_grants: list[str],
        agent_id: str | None = None,
    ) -> AgentRecord:
        with self._lock:
            aid = agent_id or f"agent-{uuid.uuid4().hex[:8]}"
            rec = AgentRecord(
                agent_id=aid,
                name=name,
                agency=agency,
                tool_grants=set(tool_grants),
            )
            self.agents[aid] = rec
            return rec

    def set_grants(self, agent_id: str, grants: list[str]) -> AgentRecord:
        with self._lock:
            agent = self.agents[agent_id]
            agent.tool_grants = set(grants)
            agent.token_version += 1
            return agent

    def grant_tool(self, agent_id: str, tool: str) -> AgentRecord:
        with self._lock:
            agent = self.agents[agent_id]
            agent.tool_grants.add(tool)
            agent.token_version += 1
            return agent

    def revoke_tool(self, agent_id: str, tool: str) -> AgentRecord:
        with self._lock:
            agent = self.agents[agent_id]
            agent.tool_grants.discard(tool)
            # Keep token_version stable so the mid-task Bearer token still
            # authenticates; subsequent calls to this tool hit 403 AML.T0053.
            return agent

    def revoke_all(self, agent_id: str) -> AgentRecord:
        with self._lock:
            agent = self.agents[agent_id]
            agent.tool_grants.clear()
            agent.token_version += 1
            return agent

    def issue_token(self, agent_id: str, ttl_seconds: int = 3600) -> TokenRecord:
        with self._lock:
            agent = self.agents[agent_id]
            if not agent.active:
                raise ValueError("Agent is inactive")
            token = f"gr_{secrets.token_urlsafe(24)}"
            rec = TokenRecord(
                token=token,
                agent_id=agent_id,
                token_version=agent.token_version,
                expires_at=time.time() + ttl_seconds,
            )
            self.tokens[token] = rec
            return rec

    def resolve_token(self, token: str) -> tuple[AgentRecord, TokenRecord] | None:
        with self._lock:
            tok = self.tokens.get(token)
            if not tok:
                return None
            if tok.expires_at < time.time():
                return None
            agent = self.agents.get(tok.agent_id)
            if not agent or not agent.active:
                return None
            if tok.token_version != agent.token_version:
                # Mid-task revoke / grant change invalidated this token version.
                return None
            return agent, tok

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
