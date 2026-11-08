"""What the sidebar shows about each conversation: its title, pin, group, archive and delete marks.

A conversation is a session. Its metadata is a separate item, `CONVO#<sid>` / `META`, owned by the
staffer the session acts for, so the session item (a shared contract) is never changed by the UI.

Titles: after the staffer's first message, and again after their third, a small model (Claude
Haiku 5.5 on Bedrock) names the staffer's main goal in a few words. It sees only the staffer's own
messages, never the agent's replies or email text. Anything that goes wrong falls back to the first
words of the first message. A title the staffer typed is never replaced.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable
from typing import Literal

from identity_gateway.gateway import get_gateway
from pydantic import BaseModel

TITLE_WORDS = 6
TITLE_CHARS = 40
REFRESH_AT = (1, 3)  # name it once there is 1 staffer message, rename once there are 3
# Claude Haiku 5.5 through the us. cross-region inference profile (listed ACTIVE in us-east-1; lead's
# ruling). Override with HOMEFRONT_TITLE_MODEL_ID; if a call fails the first-words title is used.
TITLE_MODEL_ID = os.environ.get("HOMEFRONT_TITLE_MODEL_ID", "us.anthropic.claude-haiku-5-5")
TITLE_PROMPT = (
    "You name a conversation in a constituent-services office's assistant. Read the staffer's "
    "messages and reply with only a short title (at most 6 words) for their main goal, such as "
    "'Reply to pothole complaints'. No quotes, no punctuation at the end, nothing else."
)

TitleModel = Callable[[list[str]], str]


class ConversationMeta(BaseModel):
    acting_for: str
    title: str | None = None
    title_source: Literal["auto", "fallback", "user"] | None = None
    title_basis: int = 0  # how many of the staffer's messages the title was made from
    pinned: bool = False
    group: str | None = None
    archived: bool = False
    deleted: bool = False
    # A conversation that outlives its 1-hour session continues in a new one (a chain of sessions).
    # The first link (the root) holds the title, pin, group, archive and delete marks for the whole chain.
    continues_from: str | None = None
    continued_by: str | None = None


def _key(sid: str) -> dict[str, str]:
    return {"pk": f"CONVO#{sid}", "sk": "META"}


def get_meta(sid: str, staffer_id: str) -> ConversationMeta:
    item = get_gateway().store.table.get_item(Key=_key(sid), ConsistentRead=True).get("Item")
    return ConversationMeta.model_validate(item) if item else ConversationMeta(acting_for=staffer_id)


def _set(sid: str, staffer_id: str, fields: dict, condition: str | None = None, values: dict | None = None) -> None:
    """Write only these fields (never the whole item), so writers can't undo each other's changes."""
    names = {f"#{name}": name for name in fields} | {"#owner": "acting_for"}
    if condition:
        names |= {"#title_source": "title_source", "#deleted": "deleted", "#title_basis": "title_basis"}
    assignments = [f"#{name} = :{name}" for name in fields] + ["#owner = if_not_exists(#owner, :owner)"]
    kwargs = {
        "Key": _key(sid),
        "UpdateExpression": "SET " + ", ".join(assignments),
        "ExpressionAttributeNames": names,
        "ExpressionAttributeValues": {f":{name}": value for name, value in fields.items()} | {":owner": staffer_id} | (values or {}),
    }
    if condition:
        kwargs["ConditionExpression"] = condition
    get_gateway().store.table.update_item(**kwargs)


def update_meta(sid: str, staffer_id: str, changes: dict) -> ConversationMeta:
    """Apply the staffer's changes. The result must still be a valid ConversationMeta, or nothing is written."""
    merged = ConversationMeta.model_validate({**get_meta(sid, staffer_id).model_dump(), **changes})
    _set(sid, staffer_id, {name: getattr(merged, name) for name in changes})
    return get_meta(sid, staffer_id)


def put_title_if_allowed(sid: str, staffer_id: str, title: str, source: str, basis: int) -> None:
    """The automatic title, unless meanwhile the staffer renamed or deleted the conversation, or a title
    made from more of their messages was written (a slow first call must not undo the newer one)."""
    table = get_gateway().store.table
    fields = {"title": title, "title_source": source, "title_basis": basis}
    try:
        _set(
            sid, staffer_id, fields,
            condition="(attribute_not_exists(#title_source) OR #title_source <> :user) "
            "AND (attribute_not_exists(#deleted) OR #deleted = :no) "
            "AND (attribute_not_exists(#title_basis) OR #title_basis <= :basis)",
            values={":user": "user", ":no": False, ":basis": basis},
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return


# --- chains: one conversation over several 1-hour sessions -----------------------------------

MAX_CHAIN = 200  # a bound on walking links, far above any real conversation


def root_of(sid: str, staffer_id: str) -> str:
    """The first session of the conversation `sid` belongs to."""
    for _ in range(MAX_CHAIN):
        previous = get_meta(sid, staffer_id).continues_from
        if not previous:
            return sid
        sid = previous
    return sid


def is_deleted(sid: str, staffer_id: str) -> bool:
    """Deleting a conversation marks its root, so every link of it is gone for the staffer."""
    return get_meta(root_of(sid, staffer_id), staffer_id).deleted


def claim_continuation(old_sid: str, new_sid: str, staffer_id: str) -> bool:
    """Mark `old_sid` as continued by `new_sid`, unless something already continued it (two tabs)."""
    table = get_gateway().store.table
    try:
        table.update_item(
            Key=_key(old_sid),
            UpdateExpression="SET #next = :next, #owner = if_not_exists(#owner, :owner)",
            ConditionExpression="attribute_not_exists(#next) OR #next = :none",
            ExpressionAttributeNames={"#next": "continued_by", "#owner": "acting_for"},
            ExpressionAttributeValues={":next": new_sid, ":owner": staffer_id, ":none": None},
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return False
    return True


def link_back(new_sid: str, old_sid: str, staffer_id: str) -> None:
    _set(new_sid, staffer_id, {"continues_from": old_sid})


# --- titles ------------------------------------------------------------------------------


def plain_text(text: str) -> str:
    """One line, no control or format characters (escape codes, NULs, zero-width marks)."""
    kept = "".join(" " if unicodedata.category(ch).startswith("C") else ch for ch in text)
    return " ".join(kept.split())


def clean_title(text: str) -> str:
    """At most TITLE_WORDS words and TITLE_CHARS characters, without quotes or a 'Title:' label."""
    text = re.sub(r"^\s*title\s*:\s*", "", plain_text(text), flags=re.IGNORECASE).strip(" \"'“”‘’.")
    words = text.split()[:TITLE_WORDS]
    while len(words) > 1 and len(" ".join(words)) > TITLE_CHARS:
        words.pop()
    return " ".join(words)[:TITLE_CHARS].strip()


def bedrock_title(staffer_messages: list[str], client=None) -> str:
    """Ask Claude Haiku 5.5 for a title. Only the staffer's own messages are sent."""
    if client is None:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    response = client.converse(
        modelId=TITLE_MODEL_ID,
        system=[{"text": TITLE_PROMPT}],
        messages=[{"role": "user", "content": [{"text": "\n\n".join(staffer_messages)}]}],
        inferenceConfig={"maxTokens": 30},
        # Haiku 5.5 thinks by default; off (allowed at its default effort) so the 30 tokens are the title.
        additionalModelRequestFields={"thinking": {"type": "disabled"}},
    )
    return next(block["text"] for block in response["output"]["message"]["content"] if "text" in block)


def title_model_from_env() -> TitleModel | None:
    """On unless HOMEFRONT_AUTO_TITLE is 0/false/no/off (tests set it to 0)."""
    flag = os.environ.get("HOMEFRONT_AUTO_TITLE", "1").strip().lower()
    return None if flag in {"0", "false", "no", "off"} else bedrock_title


_model: TitleModel | None = None
_configured = False


def get_title_model() -> TitleModel | None:
    global _model, _configured
    if not _configured:
        _model, _configured = title_model_from_env(), True
    return _model


def set_title_model(model: TitleModel | None) -> None:
    global _model, _configured
    _model, _configured = model, True


def staffer_messages(sid: str) -> list[str]:
    messages, _ = get_gateway().store.chat_since(sid, None)
    return [m.text for m in messages if m.role == "user"]


def fallback_title(sid: str) -> str:
    first = get_gateway().store.first_chat_message(sid)
    return (clean_title(first.text) if first else "") or "New conversation"


def refresh_title(sid: str, staffer_id: str) -> None:
    """After the staffer's 1st and 3rd message: name the conversation, unless they named it."""
    meta = get_meta(sid, staffer_id)
    mine = staffer_messages(sid)
    due = max((level for level in REFRESH_AT if len(mine) >= level), default=0)
    if meta.continues_from:
        return  # a later session of a conversation that already has its title (kept on the first session)
    if meta.title_source == "user" or meta.deleted or not due or meta.title_basis >= due:
        return  # (counting, not matching: two messages that land together don't skip a title)
    title, source = "", "auto"
    model = get_title_model()
    if model is not None:
        try:
            title = clean_title(model(mine))
        except Exception:  # Bedrock throttled, no credentials, odd answer: the fallback covers it
            title = ""
    if not title:  # keep the model's earlier title if there is one, else the first words
        title, source = (meta.title, "auto") if meta.title_source == "auto" else (fallback_title(sid), "fallback")
    put_title_if_allowed(sid, staffer_id, title, source, due)  # records `due`, so a failing model isn't retried every message
