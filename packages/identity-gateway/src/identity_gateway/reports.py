"""Reports agents save with the report.save tool (e.g. market research).

A report belongs to the agent that saved it; the API shows it to whoever can
see that agent (`GET /agents/{id}/reports`). The agent id comes from the
caller's workload token, never from tool arguments, so an agent can't write
into another agent's reports.

Backends: in-memory (default) and DynamoDB (`<prefix>reports`, pk agent_id,
sk "<ts>#<id>", newest first by Query).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from threading import Lock
from typing import Any, Protocol

from boto3.dynamodb.conditions import Key

MEMORY_LIMIT_PER_AGENT = 200


@dataclass
class ReportRecord:
    agent_id: str
    title: str
    body: str
    # Run id for agent runs, or the workload-token session for manual calls.
    trace_id: str | None = None
    run_id: str | None = None
    report_id: str = field(default_factory=lambda: f"rpt_{uuid.uuid4().hex[:12]}")
    created_at: float = field(default_factory=time.time)

    def view(self) -> dict[str, Any]:
        return asdict(self)


class ReportRepository(Protocol):
    backend: str

    def insert(self, rec: ReportRecord) -> ReportRecord: ...

    def list(self, agent_id: str, limit: int = 50) -> list[ReportRecord]:
        """Newest first."""
        ...


class MemoryReportRepository:
    backend = "memory"

    def __init__(self, limit_per_agent: int = MEMORY_LIMIT_PER_AGENT) -> None:
        self._lock = Lock()
        self._items: dict[str, list[ReportRecord]] = {}
        self._limit = limit_per_agent

    def insert(self, rec: ReportRecord) -> ReportRecord:
        with self._lock:
            items = self._items.setdefault(rec.agent_id, [])
            items.insert(0, ReportRecord(**asdict(rec)))
            del items[self._limit :]
        return rec

    def list(self, agent_id: str, limit: int = 50) -> list[ReportRecord]:
        with self._lock:
            return [ReportRecord(**asdict(r)) for r in self._items.get(agent_id, [])[:limit]]


def _sort_key(rec: ReportRecord) -> str:
    # Fixed-width so string order is time order (as in the audit table).
    return f"{rec.created_at:018.6f}#{rec.report_id}"


class DynamoReportRepository:
    """`<prefix>reports` table: pk agent_id, sk "<ts>#<id>"."""

    backend = "dynamodb"

    def __init__(self, table: Any) -> None:
        self._table = table

    def insert(self, rec: ReportRecord) -> ReportRecord:
        item = {k: v for k, v in asdict(rec).items() if v is not None}
        item["created_at"] = Decimal(str(rec.created_at))
        item["sk"] = _sort_key(rec)
        self._table.put_item(Item=item)
        return rec

    def list(self, agent_id: str, limit: int = 50) -> list[ReportRecord]:
        res = self._table.query(
            KeyConditionExpression=Key("agent_id").eq(agent_id),
            ScanIndexForward=False,
            Limit=max(1, limit),
        )
        out = []
        for item in res.get("Items", []):
            known = ReportRecord.__dataclass_fields__
            data = {k: v for k, v in item.items() if k in known}
            data["created_at"] = float(data.get("created_at") or 0)
            out.append(ReportRecord(**data))
        return out
