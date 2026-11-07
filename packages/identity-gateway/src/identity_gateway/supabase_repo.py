"""Supabase-backed agent repository (table: public.agents).

Pass a service-role client: the gateway resolves agent workload tokens with
no user session, so it reads across owners. Ownership checks for user-facing
routes happen in the API layer (and RLS, for user-scoped clients).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from postgrest.exceptions import APIError

from .store import AgentRecord, DuplicateAgentError

TABLE = "agents"
_UNIQUE_VIOLATION = "23505"


def agent_to_row(agent: AgentRecord) -> dict[str, Any]:
    # created_at / updated_at are owned by the database.
    return {
        "id": agent.agent_id,
        "owner_id": agent.owner_id,
        "name": agent.name,
        "agency": agent.agency,
        "instructions": agent.instructions,
        "model": agent.model,
        "max_steps": agent.max_steps,
        "tool_grants": sorted(agent.tool_grants),
        "token_version": agent.token_version,
        "active": agent.active,
    }


def agent_from_row(row: dict[str, Any]) -> AgentRecord:
    created = row.get("created_at")
    return AgentRecord(
        agent_id=row["id"],
        owner_id=row.get("owner_id"),
        name=row["name"],
        agency=row["agency"],
        instructions=row.get("instructions") or "",
        model=row.get("model"),
        max_steps=row["max_steps"],
        tool_grants=set(row.get("tool_grants") or []),
        token_version=row["token_version"],
        active=row["active"],
        created_at=datetime.fromisoformat(created).timestamp() if created else 0.0,
    )


class SupabaseAgentRepository:
    def __init__(self, client: Any) -> None:
        self._db = client

    def _table(self):
        return self._db.table(TABLE)

    def list(self) -> list[AgentRecord]:
        res = self._table().select("*").order("created_at").execute()
        return [agent_from_row(r) for r in res.data]

    def get(self, agent_id: str) -> AgentRecord | None:
        res = self._table().select("*").eq("id", agent_id).limit(1).execute()
        return agent_from_row(res.data[0]) if res.data else None

    def insert(self, agent: AgentRecord) -> AgentRecord:
        try:
            res = self._table().insert(agent_to_row(agent)).execute()
        except APIError as exc:
            if exc.code == _UNIQUE_VIOLATION:
                raise DuplicateAgentError(agent.agent_id) from exc
            raise
        return agent_from_row(res.data[0])

    def save(self, agent: AgentRecord) -> AgentRecord:
        row = agent_to_row(agent)
        row.pop("id")
        res = self._table().update(row).eq("id", agent.agent_id).execute()
        if not res.data:
            raise KeyError(agent.agent_id)
        return agent_from_row(res.data[0])

    def count(self) -> int:
        res = self._table().select("id", count="exact", head=True).execute()
        return res.count or 0
