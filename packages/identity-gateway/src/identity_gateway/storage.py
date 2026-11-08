"""DynamoDB storage for the gateway: one table, keys and items from `mandate_contracts.ddb`.

Every read uses ConsistentRead=True. That is what makes revocation instant: a
staffer offboarded a millisecond ago is already inactive on the next tool call.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from mandate_contracts import DecisionEvent
from mandate_contracts.ddb import (
    TABLE_NAME,
    AutonomyItem,
    CaseItem,
    ChatMessageItem,
    DraftItem,
    EmailItem,
    SessionItem,
    StafferItem,
    autonomy_key,
    case_key,
    chat_key,
    draft_key,
    email_key,
    event_key,
    session_key,
    staffer_key,
)

MAX_EVENT_DAYS = 7  # how far back a `since` cursor may reach


def create_table(dynamodb_resource: Any, name: str = TABLE_NAME) -> Any:
    """Create the single `mandate` table (pk/sk strings, on-demand). Used by tests."""
    table = dynamodb_resource.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


class MandateStore:
    def __init__(self, table: Any) -> None:
        self.table = table

    @classmethod
    def from_env(cls) -> "MandateStore":
        import boto3

        resource = boto3.resource(
            "dynamodb",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            endpoint_url=os.environ.get("MANDATE_DDB_ENDPOINT_URL") or None,
        )
        return cls(resource.Table(os.environ.get("MANDATE_TABLE", TABLE_NAME)))

    # --- generic helpers -------------------------------------------------

    def _get(self, key: dict[str, str]) -> dict[str, Any] | None:
        return self.table.get_item(Key=key, ConsistentRead=True).get("Item")

    def _put(self, key: dict[str, str], item: Any) -> None:
        self.table.put_item(Item={**key, **item.model_dump(mode="json", by_alias=True)})

    def _scan(self, prefix: str, extra: Any = None) -> list[dict[str, Any]]:
        condition = Attr("pk").begins_with(prefix)
        if extra is not None:
            condition = condition & extra
        kwargs: dict[str, Any] = {"FilterExpression": condition, "ConsistentRead": True}
        items: list[dict[str, Any]] = []
        while True:
            page = self.table.scan(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    # --- sessions and staffers --------------------------------------------

    def get_session(self, sid: str) -> SessionItem | None:
        item = self._get(session_key(sid))
        return SessionItem.model_validate(item) if item else None

    def put_session(self, sid: str, item: SessionItem) -> None:
        self._put(session_key(sid), item)

    def get_staffer(self, staffer_id: str) -> StafferItem | None:
        item = self._get(staffer_key(staffer_id))
        return StafferItem.model_validate(item) if item else None

    def put_staffer(self, staffer_id: str, item: StafferItem) -> None:
        self._put(staffer_key(staffer_id), item)

    def set_staffer_active(self, staffer_id: str, active: bool) -> None:
        self.table.update_item(
            Key=staffer_key(staffer_id),
            UpdateExpression="SET active = :a",
            ExpressionAttributeValues={":a": active},
            ConditionExpression=Attr("pk").exists(),  # never create a half-empty staffer
        )

    def revoke_sessions_for(self, staffer_id: str) -> int:
        """Mark every active session acting for this staffer as revoked; return how many."""
        active = self._scan("SESSION#", Attr("actingFor").eq(staffer_id) & Attr("status").eq("active"))
        for item in active:
            self.table.update_item(
                Key={"pk": item["pk"], "sk": item["sk"]},
                UpdateExpression="SET #s = :r",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":r": "revoked"},
            )
        self.delete_grants_for(staffer_id)
        return len(active)

    # --- casework data -----------------------------------------------------

    def get_case(self, case_id: str) -> CaseItem | None:
        item = self._get(case_key(case_id))
        return CaseItem.model_validate(item) if item else None

    def put_case(self, case_id: str, item: CaseItem) -> None:
        self._put(case_key(case_id), item)

    def get_email(self, email_id: str) -> EmailItem | None:
        item = self._get(email_key(email_id))
        return EmailItem.model_validate(item) if item else None

    def put_email(self, email_id: str, item: EmailItem) -> None:
        self._put(email_key(email_id), item)

    def constituent_emails(self, office: str) -> set[str]:
        """Addresses on file for this office's cases. Decides Cedar's `recipientKnown`."""
        cases = self._scan("CASE#", Attr("office").eq(office))
        return {item["constituentEmail"].strip().lower() for item in cases if item.get("constituentEmail")}

    # --- drafts held for review and earned autonomy ------------------------

    def get_draft(self, draft_id: str) -> DraftItem | None:
        item = self._get(draft_key(draft_id))
        return DraftItem.model_validate(item) if item else None

    def put_draft(self, draft_id: str, item: DraftItem) -> None:
        self._put(draft_key(draft_id), item)

    def decide_draft(self, draft_id: str, item: DraftItem) -> bool:
        """Write the decided draft only if it is still pending, so a draft is sent at most once."""
        try:
            self.table.put_item(
                Item={**draft_key(draft_id), **item.model_dump(mode="json", by_alias=True)},
                ConditionExpression=Attr("status").eq("pending"),
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def pending_drafts_for(self, staffer_id: str) -> list[tuple[str, DraftItem]]:
        """(draft id, draft) pairs waiting for this staffer, oldest first."""
        items = self._scan("DRAFT#", Attr("actingFor").eq(staffer_id) & Attr("status").eq("pending"))
        drafts = [(item["pk"].removeprefix("DRAFT#"), DraftItem.model_validate(item)) for item in items]
        return sorted(drafts, key=lambda pair: pair[1].created_at)

    def get_autonomy(self, staffer_id: str, category: str) -> AutonomyItem:
        item = self._get(autonomy_key(staffer_id, category))
        return AutonomyItem.model_validate(item) if item else AutonomyItem()

    def put_autonomy(self, staffer_id: str, category: str, item: AutonomyItem) -> None:
        self._put(autonomy_key(staffer_id, category), item)

    # --- per-session grants -------------------------------------------------
    # "You may send these alone, for this session only." Keyed by session, so a grant can never
    # outlive it: a revoked or expired session is denied at step 2 before any grant is read, and
    # offboarding deletes the items too.

    def put_grant(self, sid: str, category: str, staffer_id: str, granted_at: str) -> None:
        self.table.put_item(
            Item={"pk": f"GRANT#{sid}", "sk": category, "actingFor": staffer_id, "grantedAt": granted_at}
        )

    def has_grant(self, sid: str, category: str) -> bool:
        return self._get({"pk": f"GRANT#{sid}", "sk": category}) is not None

    def grants_for(self, sid: str) -> list[str]:
        page = self.table.query(KeyConditionExpression=Key("pk").eq(f"GRANT#{sid}"), ConsistentRead=True)
        return [item["sk"] for item in page.get("Items", [])]

    def delete_grants_for(self, staffer_id: str) -> int:
        grants = self._scan("GRANT#", Attr("actingFor").eq(staffer_id))
        for item in grants:
            self.table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        return len(grants)

    # --- console chat ------------------------------------------------------

    def put_chat_message(self, sid: str, message: ChatMessageItem) -> None:
        self._put(chat_key(sid, message.ts, message.id), message)

    def chat_since(self, sid: str, after: str | None) -> tuple[list[ChatMessageItem], str | None]:
        """Messages after the cursor (a sort key `<ts>#<id>`), oldest first, and the new cursor."""
        condition = Key("pk").eq(f"CHAT#{sid}")
        if after:
            condition = condition & Key("sk").gt(after)
        kwargs: dict[str, Any] = {"KeyConditionExpression": condition, "ConsistentRead": True}
        items: list[dict[str, Any]] = []
        while True:
            page = self.table.query(**kwargs)
            items.extend(page.get("Items", []))
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        cursor = items[-1]["sk"] if items else after
        return [ChatMessageItem.model_validate(item) for item in items], cursor

    # --- decision events ---------------------------------------------------

    def put_event(self, event: DecisionEvent) -> None:
        self._put(event_key(event.ts, event.id), event)

    def events_since(self, since: str | None, limit: int = 200) -> tuple[list[DecisionEvent], str | None]:
        """Events after the cursor (a sort key `<ts>#<id>`), oldest first.

        Without a cursor: today's (UTC) events. The cursor in the answer is the
        last event's sort key, or `since` itself when nothing is new.
        """
        today = datetime.now(timezone.utc).date()
        start = datetime.fromisoformat(since[:10]).date() if since else today
        start = max(start, today - timedelta(days=MAX_EVENT_DAYS))
        events: list[DecisionEvent] = []
        cursor = since
        day = start
        while day <= today and len(events) < limit:
            condition = Key("pk").eq(f"EVENTS#{day.isoformat()}")
            if since:
                condition = condition & Key("sk").gt(since)
            kwargs: dict[str, Any] = {"KeyConditionExpression": condition, "ConsistentRead": True}
            while len(events) < limit:
                page = self.table.query(**kwargs)
                for item in page.get("Items", [])[: limit - len(events)]:
                    events.append(DecisionEvent.model_validate(item))
                    cursor = item["sk"]
                if "LastEvaluatedKey" not in page:
                    break
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
            day += timedelta(days=1)
        return events, cursor
