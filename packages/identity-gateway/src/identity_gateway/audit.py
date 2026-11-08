"""Activity log storage.

Every gateway decision and every change to an agent is one audit event:

    id, ts            unique id and epoch seconds
    trace_id          groups related events: the run id for agent runs, or the
                      workload-token session for manual MCP calls
    agent_id, type    what it concerns and what happened (tool_call,
                      authenticate, agent_created, grant_revoked, run_started, ...)
    decision          allow / deny / revoke / info
    actor             who caused it: "user:<sub>", "anonymous" or "agent:<id>"
    atlas             compact ATLAS labels (ids + narrative, no long descriptions)

Two backends: in-memory (default, lost on restart) and DynamoDB
(`<prefix>audit`, pk agent_id, sk "<ts>#<id>", 90-day TTL).
"""

from __future__ import annotations

from decimal import Decimal
from threading import Lock
from typing import Any, Iterable, Protocol

from boto3.dynamodb.conditions import Attr, Key

AUDIT_TTL_SECONDS = 90 * 24 * 3600
MEMORY_LIMIT = 500


def compact_atlas(atlas: Any) -> Any:
    """Keep the ATLAS fields the log needs; drop the long technique texts."""
    if not isinstance(atlas, dict):
        return atlas
    return {
        k: atlas[k]
        for k in ("event", "technique_ids", "mitigation_ids", "narrative", "residual_risk")
        if k in atlas
    }


def matches(
    event: dict[str, Any],
    trace_id: str | None = None,
    decision: str | None = None,
    type_: str | None = None,
) -> bool:
    return (
        (trace_id is None or event.get("trace_id") == trace_id)
        and (decision is None or event.get("decision") == decision)
        and (type_ is None or event.get("type") == type_)
    )


class AuditRepository(Protocol):
    backend: str

    def append(self, event: dict[str, Any]) -> None: ...

    def list(
        self,
        agent_ids: Iterable[str],
        limit: int,
        *,
        trace_id: str | None = None,
        decision: str | None = None,
        type_: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first, only events for `agent_ids`."""
        ...


class MemoryAuditRepository:
    backend = "memory"

    def __init__(self, limit: int = MEMORY_LIMIT) -> None:
        self._lock = Lock()
        self._events: list[dict[str, Any]] = []
        self._limit = limit

    def append(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.insert(0, event)
            del self._events[self._limit :]

    def list(self, agent_ids, limit, *, trace_id=None, decision=None, type_=None):
        wanted = set(agent_ids)
        with self._lock:
            events = list(self._events)
        out = [
            e
            for e in events
            if e.get("agent_id") in wanted and matches(e, trace_id, decision, type_)
        ]
        return out[:limit]


def _to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple, set)):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def sort_key(event: dict[str, Any]) -> str:
    # Fixed-width so string order is time order.
    return f"{float(event['ts']):018.6f}#{event['id']}"


class DynamoAuditRepository:
    """`<prefix>audit` table: pk agent_id, sk "<ts>#<id>"."""

    backend = "dynamodb"
    MAX_PAGES = 20

    def __init__(self, table: Any, ttl_seconds: int = AUDIT_TTL_SECONDS) -> None:
        self._table = table
        self._ttl = ttl_seconds

    def append(self, event: dict[str, Any]) -> None:
        item = _to_dynamo(
            {
                **event,
                "agent_id": event.get("agent_id") or "_system",
                "sk": sort_key(event),
                "expires_at": int(event["ts"]) + self._ttl,
            }
        )
        self._table.put_item(Item=item)

    def list(self, agent_ids, limit, *, trace_id=None, decision=None, type_=None):
        filters = None
        for attr, value in (("trace_id", trace_id), ("decision", decision), ("type", type_)):
            if value is not None:
                cond = Attr(attr).eq(value)
                filters = cond if filters is None else filters & cond

        events: list[dict[str, Any]] = []
        for agent_id in set(agent_ids):
            events.extend(self._query(agent_id, limit, filters))
        events.sort(key=lambda e: (e.get("ts", 0), e.get("id", "")), reverse=True)
        return events[:limit]

    def _query(self, agent_id: str, limit: int, filters: Any) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("agent_id").eq(agent_id),
            "ScanIndexForward": False,  # newest first
            "Limit": max(limit, 25),
        }
        if filters is not None:
            kwargs["FilterExpression"] = filters
        out: list[dict[str, Any]] = []
        # Limit applies before the filter, so keep paging until we have enough.
        for _ in range(self.MAX_PAGES):
            res = self._table.query(**kwargs)
            out.extend(self._event(i) for i in res.get("Items", []))
            if len(out) >= limit or "LastEvaluatedKey" not in res:
                break
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]
        return out[:limit]

    @staticmethod
    def _event(item: dict[str, Any]) -> dict[str, Any]:
        event = _from_dynamo(item)
        event.pop("sk", None)
        event.pop("expires_at", None)
        if event.get("agent_id") == "_system":
            event["agent_id"] = None
        return event
