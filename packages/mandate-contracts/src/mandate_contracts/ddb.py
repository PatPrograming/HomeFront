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
