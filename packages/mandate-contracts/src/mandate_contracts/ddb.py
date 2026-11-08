"""Single DynamoDB table `mandate` (brief §6.4): key builders and item shapes.

Attributes are snake_case; items are stored in DynamoDB with camelCase keys (see wire.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .wire import WireModel

TABLE_NAME = "mandate"
META = "META"


def session_key(sid: str) -> dict[str, str]:
    return {"pk": f"SESSION#{sid}", "sk": META}


def staffer_key(staffer_id: str) -> dict[str, str]:
    return {"pk": f"STAFFER#{staffer_id}", "sk": META}


def case_key(case_id: str) -> dict[str, str]:
    return {"pk": f"CASE#{case_id}", "sk": META}


def email_key(email_id: str) -> dict[str, str]:
    return {"pk": f"EMAIL#{email_id}", "sk": META}


def draft_key(draft_id: str) -> dict[str, str]:
    return {"pk": f"DRAFT#{draft_id}", "sk": META}


def autonomy_key(staffer_id: str, category: str) -> dict[str, str]:
    """One item per staffer and category, next to the staffer's META item."""
    return {"pk": f"STAFFER#{staffer_id}", "sk": f"AUTONOMY#{category}"}


def chat_key(sid: str, ts: str, message_id: str) -> dict[str, str]:
    """The console chat for one session; `?after=` queries sk > after."""
    return {"pk": f"CHAT#{sid}", "sk": f"{ts}#{message_id}"}


def event_key(ts: str, event_id: str) -> dict[str, str]:
    """Events are partitioned by day; `GET /events?since=` queries sk > since."""
    return {"pk": f"EVENTS#{ts[:10]}", "sk": f"{ts}#{event_id}"}


class SessionItem(WireModel):
    """SESSION#<sid> / META. The broker creates it; Offboard sets status=revoked."""

    status: Literal["active", "revoked"]
    agent: str
    acting_for: str
    office: str
    mode: Literal["official", "campaign"]
    created_at: str  # ISO-8601, UTC


class StafferItem(WireModel):
    """STAFFER#<id> / META. Offboard sets active=False."""

    active: bool
    office: str
    role: Literal["staffer", "admin"]


class CaseItem(WireModel):
    """CASE#<id> / META. The gateway reads `office` from here, never from tool input."""

    office: str
    constituent: str
    constituent_email: str
    topic: str
    status: str
    notes: str = ""


class EmailItem(WireModel):
    """EMAIL#<id> / META. Body is untrusted public input."""

    from_: str = Field(alias="from")  # `from` is a Python keyword
    subject: str
    body: str
    # Set when the email is triaged into the inbox, never by the agent. Decides whether a reply
    # to it can be sent without review (see identity_gateway.autonomy).
    kind: str = "other"


class DraftItem(WireModel):
    """DRAFT#<id> / META. An outbound call the gateway held for the staffer to review."""

    status: Literal["pending", "sent", "rejected"]
    sid: str
    agent: str
    acting_for: str
    office: str
    tool: Literal["email_send", "social_post"]
    category: str
    args: dict[str, str]  # the tool input as the agent sent it
    created_at: str
    decided_at: str | None = None
    edited: bool = False  # the staffer changed the text before approving


class AutonomyItem(WireModel):
    """STAFFER#<id> / AUTONOMY#<category>. What the staffer lets the agent do alone, and the evidence."""

    level: Literal["review", "auto"] = "review"
    approved_unchanged: int = 0
    edited: int = 0
    rejected: int = 0
    snoozed_at: int = 0  # reviews counted when the staffer last said "not now" to the suggestion


class ChatMessageItem(WireModel):
    """CHAT#<sid> / <ts>#<id>. One message in the console chat."""

    id: str
    ts: str
    role: Literal["user", "assistant"]
    text: str
