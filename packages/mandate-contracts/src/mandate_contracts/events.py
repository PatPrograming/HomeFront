"""Decision event (brief §6.3), written by the gateway for every tool call.

Attributes are snake_case; the JSON in DynamoDB and the TypeScript type in
`ts/index.ts` use camelCase (see wire.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .wire import WireModel

Decision = Literal["ALLOW", "DENY"]
Reason = Literal[
    "allowed",
    "denied by policy",
    "session revoked / delegator inactive",
    "invalid token",
]


class GuardrailFlag(WireModel):
    flagged: bool = False
    categories: list[str] = Field(default_factory=list)


class DecisionEvent(WireModel):
    id: str
    ts: str  # ISO-8601, UTC
    sid: str
    agent: str
    acting_for: str
    office: str
    tool: str
    resource: str | None = None
    decision: Decision
    reason: Reason
    policy_ids: list[str] = Field(default_factory=list)
    atlas: list[str] = Field(default_factory=list)
    guardrail: GuardrailFlag = Field(default_factory=GuardrailFlag)
