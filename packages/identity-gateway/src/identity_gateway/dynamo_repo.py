"""DynamoDB-backed agent repository (table: <prefix>agents, pk `id`).

Pass a boto3 Table resource. The gateway reads across owners (it resolves
agent workload tokens with no user session); owner scoping happens in the API
layer (`Actor.can_access`), and the table is reachable only with the backend's
IAM role.

Reads use ConsistentRead so a grant revoke is visible to the very next tool call.
The check constraints the Postgres table had (id format, field lengths,
max_steps range) are enforced by the API's request models.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from botocore.exceptions import ClientError

from .store import AgentRecord, DuplicateAgentError, EmailSettings, demo_agent


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def agent_to_item(agent: AgentRecord) -> dict[str, Any]:
    return {
        "id": agent.agent_id,
        "owner_id": agent.owner_id,
        "kind": agent.kind,
        "name": agent.name,
        "agency": agent.agency,
        "instructions": agent.instructions,
        "model": agent.model,
        "max_steps": agent.max_steps,
        # A list, not a string set: DynamoDB rejects empty sets.
        "tool_grants": sorted(agent.tool_grants),
        "recipients": list(agent.recipients),
        "email_settings": agent.email_settings.view(),
        "social_connections": {k: dict(v) for k, v in agent.social_connections.items()},
        "token_version": agent.token_version,
        "active": agent.active,
        "created_at": _iso(agent.created_at),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def agent_from_item(item: dict[str, Any]) -> AgentRecord:
    created = item.get("created_at")
    return AgentRecord(
        agent_id=item["id"],
        owner_id=item.get("owner_id"),
        # Rows written before agent types existed are custom agents.
        kind=item.get("kind") or "custom",
        name=item["name"],
        agency=item["agency"],
        instructions=item.get("instructions") or "",
        model=item.get("model"),
        max_steps=int(item["max_steps"]),
        tool_grants=set(item.get("tool_grants") or []),
        recipients=list(item.get("recipients") or []),
        # Rows written before email settings existed have none.
        email_settings=EmailSettings.from_dict(item.get("email_settings")),
        # Rows written before social connections existed have none.
        social_connections={
            str(k): {kk: (int(vv) if isinstance(vv, Decimal) else vv) for kk, vv in dict(v).items()}
            for k, v in (item.get("social_connections") or {}).items()
        },
        token_version=int(item["token_version"]),
        active=bool(item["active"]),
        created_at=datetime.fromisoformat(created).timestamp() if created else 0.0,
    )


def _conditional_failed(exc: ClientError) -> bool:
    return exc.response["Error"]["Code"] == "ConditionalCheckFailedException"


class DynamoAgentRepository:
    backend = "dynamodb"

    def __init__(self, table: Any) -> None:
        self._table = table

    def _scan(self, **kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            res = self._table.scan(ConsistentRead=True, **kwargs)
            items.extend(res.get("Items", []))
            if "LastEvaluatedKey" not in res:
                return items
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    def list(self) -> list[AgentRecord]:
        agents = [agent_from_item(i) for i in self._scan()]
        return sorted(agents, key=lambda a: a.created_at)

    def get(self, agent_id: str) -> AgentRecord | None:
        res = self._table.get_item(Key={"id": agent_id}, ConsistentRead=True)
        item = res.get("Item")
        return agent_from_item(item) if item else None

    def insert(self, agent: AgentRecord) -> AgentRecord:
        item = agent_to_item(agent)
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
        except ClientError as exc:
            if _conditional_failed(exc):
                raise DuplicateAgentError(agent.agent_id) from exc
            raise
        return agent_from_item(item)

    def save(self, agent: AgentRecord) -> AgentRecord:
        item = agent_to_item(agent)
        try:
            self._table.put_item(Item=item, ConditionExpression="attribute_exists(id)")
        except ClientError as exc:
            if _conditional_failed(exc):
                raise KeyError(agent.agent_id) from exc
            raise
        return agent_from_item(item)

    def count(self) -> int:
        total, kwargs = 0, {}
        while True:
            res = self._table.scan(Select="COUNT", **kwargs)
            total += int(res.get("Count", 0))
            if "LastEvaluatedKey" not in res:
                return total
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]


def seed_demo_agent(repo: DynamoAgentRepository) -> bool:
    """Create the shared demo agent if missing (was the migration's seed row)."""
    try:
        repo.insert(demo_agent())
        return True
    except DuplicateAgentError:
        return False
