"""Human approval for high-risk tool calls (ToolDefinition.requires_hitl).

When an agent calls such a tool (email.send, social.post, social.reply), the
gateway does not run it. It stores a pending approval and tells the agent the
action is queued. A person then approves (optionally editing the arguments) or
rejects it, and only an approval executes the tool.

Lifecycle:
    pending --approve--> approved --run--> executed | failed
    pending --reject---> rejected
    pending --(after APPROVAL_TTL_SECONDS)--> expired (checked on decision)

A request leaves `pending` exactly once (conditional write), so two people
approving at the same moment can't send it twice.

Backends: in-memory (default) and DynamoDB (`<prefix>approvals`, pk id).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from threading import Lock
from typing import Any, Literal, Protocol

from botocore.exceptions import ClientError

ApprovalStatus = Literal["pending", "approved", "executed", "failed", "rejected", "expired"]

APPROVAL_TTL_SECONDS = 24 * 3600


@dataclass
class ApprovalRecord:
    agent_id: str
    tool: str
    arguments: dict[str, Any]
    # Same trace as the tool call that asked for it (run id or token session).
    trace_id: str | None = None
    run_id: str | None = None
    approval_id: str = field(default_factory=lambda: f"apr_{uuid.uuid4().hex[:12]}")
    status: ApprovalStatus = "pending"
    requested_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + APPROVAL_TTL_SECONDS)
    decided_by: str | None = None
    decided_at: float | None = None
    note: str | None = None
    # Arguments as the agent proposed them, kept when a reviewer edits them.
    original_arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    @property
    def edited(self) -> bool:
        return self.original_arguments is not None and self.original_arguments != self.arguments

    def is_expired(self, now: float | None = None) -> bool:
        return self.status == "pending" and (now or time.time()) > self.expires_at

    def view(self) -> dict[str, Any]:
        out = asdict(self)
        out["edited"] = self.edited
        if self.is_expired():
            out["status"] = "expired"
        return out


class ApprovalConflict(Exception):
    """The approval was no longer in the expected state (already decided)."""


class ApprovalRepository(Protocol):
    backend: str

    def insert(self, rec: ApprovalRecord) -> ApprovalRecord: ...

    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def list(self) -> list[ApprovalRecord]: ...

    def transition(
        self, approval_id: str, expected: str, **changes: Any
    ) -> ApprovalRecord:
        """Apply `changes` only if status == expected; else ApprovalConflict."""
        ...


def _copy(rec: ApprovalRecord) -> ApprovalRecord:
    # asdict deep-copies, so callers can't mutate stored arguments.
    return ApprovalRecord(**asdict(rec))


class MemoryApprovalRepository:
    backend = "memory"

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[str, ApprovalRecord] = {}

    def insert(self, rec: ApprovalRecord) -> ApprovalRecord:
        with self._lock:
            self._items[rec.approval_id] = _copy(rec)
        return _copy(rec)

    def get(self, approval_id: str) -> ApprovalRecord | None:
        with self._lock:
            rec = self._items.get(approval_id)
            return _copy(rec) if rec else None

    def list(self) -> list[ApprovalRecord]:
        with self._lock:
            return [_copy(r) for r in self._items.values()]

    def transition(self, approval_id: str, expected: str, **changes: Any) -> ApprovalRecord:
        with self._lock:
            rec = self._items.get(approval_id)
            if rec is None:
                raise KeyError(approval_id)
            if rec.status != expected:
                raise ApprovalConflict(rec.status)
            for k, v in changes.items():
                setattr(rec, k, v)
            return _copy(rec)


def _to_dynamo(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
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


def _record(item: dict[str, Any]) -> ApprovalRecord:
    data = _from_dynamo(item)
    data["approval_id"] = data.pop("id")
    known = ApprovalRecord.__dataclass_fields__
    rec = ApprovalRecord(**{k: v for k, v in data.items() if k in known})
    for ts in ("requested_at", "expires_at", "decided_at"):
        value = getattr(rec, ts)
        if value is not None:
            setattr(rec, ts, float(value))
    return rec


class DynamoApprovalRepository:
    """`<prefix>approvals` table, pk `id`."""

    backend = "dynamodb"

    def __init__(self, table: Any) -> None:
        self._table = table

    def insert(self, rec: ApprovalRecord) -> ApprovalRecord:
        item = _to_dynamo({**asdict(rec), "id": rec.approval_id})
        item.pop("approval_id", None)
        self._table.put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
        return rec

    def get(self, approval_id: str) -> ApprovalRecord | None:
        res = self._table.get_item(Key={"id": approval_id}, ConsistentRead=True)
        item = res.get("Item")
        return _record(item) if item else None

    def list(self) -> list[ApprovalRecord]:
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"ConsistentRead": True}
        while True:
            res = self._table.scan(**kwargs)
            items.extend(res.get("Items", []))
            if "LastEvaluatedKey" not in res:
                break
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]
        return [_record(i) for i in items]

    def transition(self, approval_id: str, expected: str, **changes: Any) -> ApprovalRecord:
        sets, removes = [], []
        names: dict[str, str] = {"#status": "status"}
        values: dict[str, Any] = {":expected": expected}
        for i, (key, value) in enumerate(changes.items()):
            placeholder = "#status" if key == "status" else f"#f{i}"
            names[placeholder] = key
            if value is None:
                removes.append(placeholder)
            else:
                sets.append(f"{placeholder} = :v{i}")
                values[f":v{i}"] = _to_dynamo(value)
        expr = ""
        if sets:
            expr += "SET " + ", ".join(sets)
        if removes:
            expr += " REMOVE " + ", ".join(removes)
        try:
            res = self._table.update_item(
                Key={"id": approval_id},
                UpdateExpression=expr.strip(),
                ConditionExpression="attribute_exists(id) AND #status = :expected",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            current = self.get(approval_id)
            if current is None:
                raise KeyError(approval_id) from exc
            raise ApprovalConflict(current.status) from exc
        return _record(res["Attributes"])
