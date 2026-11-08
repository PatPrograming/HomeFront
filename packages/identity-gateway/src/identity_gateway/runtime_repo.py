"""Gateway state that must survive a process: workload tokens, taint marks and runs.

Locally the gateway is one long-running process and keeps these in memory (the
`Memory*` classes, the default). On AWS Lambda every request may land on a different,
short-lived process, so they go to one DynamoDB table instead (`<prefix>runtime`):

    id (S, hash key)                 data (S, JSON)            expires (N, TTL)
    token#<sha256 of the token>      TokenRecord               token expiry
    taint#<trace id>                 {"tool", "ts"}            7 days
    run#<run id>                     RunRecord                 7 days

Values are stored as a JSON string: DynamoDB rejects Python floats, and nothing here is
queried by its fields except runs by agent (a filtered scan; the table stays small).
Taint is write-once (a conditional put): the first untrusted read in a trace wins, and a
mark, once written, is seen by every process. That is what keeps `tainted_session`
fail-closed across Lambda instances.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from threading import Lock
from typing import Any, Protocol

from botocore.exceptions import ClientError

from .runner import RunRecord
from .store import TokenRecord

TAINT_TTL_SECONDS = 7 * 24 * 3600
RUN_TTL_SECONDS = 7 * 24 * 3600
RUN_LIST_LIMIT = 200


class TokenRepository(Protocol):
    def put(self, rec: TokenRecord) -> None: ...
    def get(self, token: str) -> TokenRecord | None: ...


class TaintRepository(Protocol):
    def mark(self, trace_id: str, tool: str) -> None: ...
    def get(self, trace_id: str) -> dict[str, Any] | None: ...


# --- memory (one process) ---------------------------------------------------


class MemoryTokenRepository:
    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[str, TokenRecord] = {}

    def put(self, rec: TokenRecord) -> None:
        with self._lock:
            self._items[rec.token] = rec

    def get(self, token: str) -> TokenRecord | None:
        with self._lock:
            return self._items.get(token)


class MemoryTaintRepository:
    MAX_TAINTED = 10_000

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: dict[str, dict[str, Any]] = {}

    def mark(self, trace_id: str, tool: str) -> None:
        with self._lock:
            if trace_id in self._items:
                return
            self._items[trace_id] = {"tool": tool, "ts": time.time()}
            while len(self._items) > self.MAX_TAINTED:
                self._items.pop(next(iter(self._items)))

    def get(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock:
            taint = self._items.get(trace_id)
            return dict(taint) if taint else None


# --- DynamoDB (many processes) ----------------------------------------------


def _token_key(token: str) -> str:
    return "token#" + hashlib.sha256(token.encode()).hexdigest()


class DynamoTokenRepository:
    def __init__(self, table: Any) -> None:
        self.table = table

    def put(self, rec: TokenRecord) -> None:
        self.table.put_item(
            Item={"id": _token_key(rec.token), "data": json.dumps(asdict(rec)), "expires": int(rec.expires_at) + 60}
        )

    def get(self, token: str) -> TokenRecord | None:
        item = self.table.get_item(Key={"id": _token_key(token)}, ConsistentRead=True).get("Item")
        return TokenRecord(**json.loads(item["data"])) if item else None


class DynamoTaintRepository:
    def __init__(self, table: Any) -> None:
        self.table = table

    def mark(self, trace_id: str, tool: str) -> None:
        now = time.time()
        try:
            self.table.put_item(
                Item={"id": f"taint#{trace_id}", "data": json.dumps({"tool": tool, "ts": now}),
                      "expires": int(now) + TAINT_TTL_SECONDS},
                ConditionExpression="attribute_not_exists(id)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise  # fail loudly: an unrecorded taint must not pass silently

    def get(self, trace_id: str) -> dict[str, Any] | None:
        item = self.table.get_item(Key={"id": f"taint#{trace_id}"}, ConsistentRead=True).get("Item")
        return json.loads(item["data"]) if item else None


class DynamoRunStore:
    """Same interface as runner.RunStore, plus `save` for progress written mid-run."""

    def __init__(self, table: Any) -> None:
        self.table = table

    def add(self, run: RunRecord) -> RunRecord:
        self.save(run)
        return run

    def save(self, run: RunRecord) -> None:
        self.table.put_item(
            Item={"id": f"run#{run.run_id}", "kind": "run", "agent_id": run.agent_id,
                  "data": json.dumps(asdict(run), default=str),
                  "expires": int(run.created_at) + RUN_TTL_SECONDS}
        )

    def get(self, run_id: str) -> RunRecord | None:
        item = self.table.get_item(Key={"id": f"run#{run_id}"}, ConsistentRead=True).get("Item")
        return RunRecord(**json.loads(item["data"])) if item else None

    def for_agent(self, agent_id: str) -> list[RunRecord]:
        from boto3.dynamodb.conditions import Attr

        runs: list[RunRecord] = []
        kwargs: dict[str, Any] = {"FilterExpression": Attr("kind").eq("run") & Attr("agent_id").eq(agent_id)}
        while True:
            page = self.table.scan(**kwargs)
            runs.extend(RunRecord(**json.loads(i["data"])) for i in page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:RUN_LIST_LIMIT]


RUNTIME_TABLE_SPEC: dict[str, Any] = {
    "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
    "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"}],
}
