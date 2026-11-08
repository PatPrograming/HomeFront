"""User profiles in DynamoDB.

Replaces the Postgres `profiles` table:
- Signup trigger -> `get_or_create`: the first authenticated request creates
  the row from the token's `name` claim (conditional put, so concurrent first
  requests can't clobber each other).
- Row-level security -> every call is keyed by the verified token's `sub`;
  there is no way to address another user's row through this module.
- `username unique` -> a reservation row in the usernames table, written in the
  same transaction as the profile update.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import ClientError

from app.core.auth import CurrentUser
from app.core.dynamodb import table, table_name
from app.schemas.profile import ProfileUpdate

PROFILE_FIELDS = ("username", "full_name", "avatar_url")


class UsernameTaken(Exception):
    pass


class ProfileConflict(Exception):
    """The profile changed concurrently; the caller may retry."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_profile(user_id: str) -> dict | None:
    res = table("profiles").get_item(Key={"id": user_id}, ConsistentRead=True)
    return res.get("Item")


def get_or_create_profile(user: CurrentUser) -> dict:
    existing = get_profile(user.id)
    if existing:
        return existing
    now = _now()
    item = {
        "id": user.id,
        "full_name": user.name,
        "avatar_url": None,
        "username": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        table("profiles").put_item(Item=item, ConditionExpression="attribute_not_exists(id)")
        return item
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Another request created it first.
        return get_profile(user.id)


def update_profile(user: CurrentUser, changes: ProfileUpdate) -> dict:
    current = get_or_create_profile(user)
    payload = changes.model_dump(exclude_unset=True)
    if not payload:
        return current

    names: dict[str, str] = {"#updated_at": "updated_at"}
    values: dict[str, Any] = {":updated_at": _now()}
    sets = ["#updated_at = :updated_at"]
    for field in PROFILE_FIELDS:
        if field in payload:
            names[f"#{field}"] = field
            values[f":{field}"] = payload[field]
            sets.append(f"#{field} = :{field}")
    update_expr = "SET " + ", ".join(sets)

    old_username = current.get("username")
    new_username = payload.get("username", old_username)

    if new_username == old_username:
        res = table("profiles").update_item(
            Key={"id": user.id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(id)",
            ReturnValues="ALL_NEW",
        )
        return res["Attributes"]

    # Username changes: reserve the new name, release the old one and update the
    # profile atomically. The profile condition guards against a concurrent
    # username change from another request.
    names["#username"] = "username"
    if old_username is None:
        # Unset usernames are stored as NULL (or absent on older rows).
        values[":null_type"] = "NULL"
        unchanged = "(attribute_not_exists(#username) OR attribute_type(#username, :null_type))"
    else:
        values[":old_username"] = old_username
        unchanged = "#username = :old_username"
    items: list[dict[str, Any]] = []
    if new_username is not None:
        items.append(
            {
                "Put": {
                    "TableName": table_name("usernames"),
                    "Item": {"username": new_username, "user_id": user.id},
                    "ConditionExpression": "attribute_not_exists(username)",
                }
            }
        )
    if old_username is not None:
        items.append(
            {
                "Delete": {
                    "TableName": table_name("usernames"),
                    "Key": {"username": old_username},
                    "ConditionExpression": "user_id = :me",
                    "ExpressionAttributeValues": {":me": user.id},
                }
            }
        )
    items.append(
        {
            "Update": {
                "TableName": table_name("profiles"),
                "Key": {"id": user.id},
                "UpdateExpression": update_expr,
                "ConditionExpression": f"attribute_exists(id) AND {unchanged}",
                "ExpressionAttributeNames": names,
                "ExpressionAttributeValues": values,
            }
        }
    )

    # The resource's client serializes plain Python values itself; passing
    # pre-serialized {"S": ...} values would double-encode them.
    client = table("profiles").meta.client
    try:
        client.transact_write_items(TransactItems=items)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = exc.response.get("CancellationReasons") or []
        if new_username is not None and reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
            raise UsernameTaken(new_username) from exc
        raise ProfileConflict() from exc
    return get_profile(user.id)
