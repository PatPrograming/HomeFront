"""What the sidebar shows about each conversation: its title, pin, group, archive and delete marks.

A conversation is a session. Its metadata is a separate item, `CONVO#<sid>` / `META`, owned by the
staffer the session acts for, so the session item (a shared contract) is never changed by the UI.

Titles: after the staffer's first message, and again after their third, a small model (Claude
Haiku 4.5 on Bedrock) names the staffer's main goal in a few words. It sees only the staffer's own
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
REFRESH_AT = (1, 3)  # the staffer's message counts that (re)name the conversation
# Claude Haiku 4.5 through the us. cross-region inference profile; confirm with
# `aws bedrock list-inference-profiles` and override with HOMEFRONT_TITLE_MODEL_ID if it differs.
TITLE_MODEL_ID = os.environ.get("HOMEFRONT_TITLE_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
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
    pinned: bool = False
    group: str | None = None
    archived: bool = False
    deleted: bool = False


def _key(sid: str) -> dict[str, str]:
    return {"pk": f"CONVO#{sid}", "sk": "META"}


def get_meta(sid: str, staffer_id: str) -> ConversationMeta:
    item = get_gateway().store.table.get_item(Key=_key(sid), ConsistentRead=True).get("Item")
    return ConversationMeta.model_validate(item) if item else ConversationMeta(acting_for=staffer_id)


def put_meta(sid: str, meta: ConversationMeta) -> None:
    get_gateway().store.table.put_item(Item={**_key(sid), **meta.model_dump()})


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
    """Ask Claude Haiku 4.5 for a title. Only the staffer's own messages are sent."""
    if client is None:
        import boto3

        client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    response = client.converse(
        modelId=TITLE_MODEL_ID,
        system=[{"text": TITLE_PROMPT}],
        messages=[{"role": "user", "content": [{"text": "\n\n".join(staffer_messages)}]}],
        inferenceConfig={"maxTokens": 30},
    )
    return response["output"]["message"]["content"][0]["text"]


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
    if meta.title_source == "user" or meta.deleted or len(mine) not in REFRESH_AT:
        return
    title, source = "", "auto"
    model = get_title_model()
    if model is not None:
        try:
            title = clean_title(model(mine))
        except Exception:  # Bedrock throttled, no credentials, odd answer: the fallback covers it
            title = ""
    if not title:
        title, source = fallback_title(sid), "fallback"
    put_meta(sid, meta.model_copy(update={"title": title, "title_source": source}))
