"""Single DynamoDB table `mandate` (brief §6.4): key builders and item shapes.

Item field names are camelCase, the same spelling stored in DynamoDB.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class SessionItem(BaseModel):
    """SESSION#<sid> / META. The broker creates it; Offboard sets status=revoked."""

    status: Literal["active", "revoked"]
    agent: str
    actingFor: str
    office: str
    mode: Literal["official", "campaign"]
    createdAt: str  # ISO-8601, UTC


class StafferItem(BaseModel):
    """STAFFER#<id> / META. Offboard sets active=False."""

    active: bool
    office: str
    role: Literal["staffer", "admin"]


class CaseItem(BaseModel):
    """CASE#<id> / META. The gateway reads `office` from here, never from tool input."""

    office: str
    constituent: str
    constituentEmail: str
    topic: str
    status: str
    notes: str = ""


class EmailItem(BaseModel):
    """EMAIL#<id> / META. Body is untrusted public input."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")  # `from` is a Python keyword
    subject: str
    body: str
