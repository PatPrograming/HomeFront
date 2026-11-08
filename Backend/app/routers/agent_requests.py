"""The Emails form hands its answers to the chat agent.

The form used to end at a route that did not exist. Now `POST /sessions/{sid}/agent-requests` turns the
staffer's answers into one chat message for their session's agent: what to do (draft, send, prioritize,
summarize), the main message, the recipients and the restrictions. The agent reads it exactly like a
message the staffer typed, so everything that already protects a typed message protects this too:

- Only the session's own staffer can post it (the same 404 / 403 as the chat).
- The agent still acts through the gateway: its mandate token, DynamoDB, Cedar and the review queue.
  The recipients are a request, not a permission. `email_send` still needs a `.gov` address or a
  constituent on file, and a send is still held for the staffer's review unless they let that
  category go alone. A recipient Cedar refuses is denied and shows in the feed.
- The route checks only the *shape* of an address (brief rule 4: let Cedar deny, so the deny is visible).
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.routers.assistant import StaffIdentity, _ensure_agent, _own_active_session, _post, require_staff

router = APIRouter(tags=["assistant"])

Action = Literal["Draft", "Send", "Prioritize", "Only summarize"]
MAX_RECIPIENTS = 25
ADDRESS = re.compile(r"^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]{2,}$")
NEEDS_RECIPIENTS: set[str] = {"Draft", "Send"}


class EmailRequestIn(BaseModel):
    """The JSON the Emails form builds (`Frontend/src/lib/agentPayload.ts`, `EmailPayload`)."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["email"]
    role: Action
    message: str = Field(min_length=1, max_length=2000)
    restrictions: str = Field(default="", max_length=1000)
    recipients: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    mailboxes: list[str] = Field(default_factory=list, max_length=10)
    mail_platforms: list[str] = Field(default_factory=list, max_length=10)
    most_common_mail_type: str = Field(default="", max_length=80)
    emails_per_day: int | None = Field(default=None, ge=0, le=10_000_000)

    @field_validator("message", "restrictions")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("recipients")
    @classmethod
    def _addresses(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(v.strip().lower() for v in values if v.strip()))
        bad = [v for v in cleaned if len(v) > 254 or not ADDRESS.match(v)]
        if bad:
            raise ValueError(f"not an email address: {', '.join(bad[:3])}")
        return cleaned

    @model_validator(mode="after")
    def _recipients_when_writing(self) -> "EmailRequestIn":
        if not self.message:
            raise ValueError("the main message is empty")
        if self.role in NEEDS_RECIPIENTS and not self.recipients:
            raise ValueError(f'"{self.role}" needs at least one recipient')
        return self


def _quote(text: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def build_brief(request: EmailRequestIn, staffer: str) -> str:
    """The chat message the agent reads. Plain text, so the console shows it as the staffer's request."""
    to = ", ".join(request.recipients)
    steps: dict[str, str] = {
        "Send": (
            "Write the email from my main message and send it with email_send, one call per recipient, "
            f"to exactly these addresses: {to}. Send to nobody else. The gateway may hold a send for my "
            "review or refuse an address; if it does, tell me which, and do not say anything was sent "
            "unless the gateway says it was."
        ),
        "Draft": (
            "Write the email from my main message and show me the full draft (subject and body) in your "
            f"reply, addressed to: {to}. Do not call email_send; I will decide whether to send it."
        ),
        "Prioritize": (
            "Read my inbox with inbox_read and rank what needs attention first, with one line on why for "
            "each email. Do not send or reply to anything."
        ),
        "Only summarize": (
            "Read my inbox with inbox_read and summarize it for me in a few lines. Do not send or reply "
            "to anything."
        ),
    }
    context = [
        f"Mailboxes: {', '.join(request.mailboxes)}" if request.mailboxes else "",
        f"Mail platform: {', '.join(request.mail_platforms)}" if request.mail_platforms else "",
        f"Most common mail: {request.most_common_mail_type}" if request.most_common_mail_type else "",
        f"About {request.emails_per_day} emails a day" if request.emails_per_day is not None else "",
    ]
    parts = [
        f"Request from the Emails form, from {staffer}. This is my instruction to you.",
        f"Task: {request.role}. {steps[request.role]}",
        "Main message (the facts the email must contain; do not add dates, figures or promises beyond them):",
        _quote(request.message),
    ]
    if request.restrictions:
        parts += [
            "My restrictions (follow them strictly; if the task would break one, do not do it and tell me why):",
            _quote(request.restrictions),
        ]
    context = [line for line in context if line]
    if context:
        parts.append("Background: " + "; ".join(context) + ".")
    return "\n\n".join(parts)


@router.post("/sessions/{sid}/agent-requests")
def submit_email_request(sid: str, body: EmailRequestIn, staff: StaffIdentity = Depends(require_staff)) -> dict:
    """Post the form's answers into the session's chat; start the agent if auto-start is on."""
    _own_active_session(sid, staff)
    _ensure_agent(sid, staff)
    message = _post(sid, "user", build_brief(body, staff.username))
    return {"sid": sid, "action": body.role, "recipients": body.recipients, "message": message}
