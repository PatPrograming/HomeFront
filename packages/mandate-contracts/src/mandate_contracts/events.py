"""Decision event (brief §6.3), written by the gateway for every tool call.

Field names are camelCase so the Python model, the JSON in DynamoDB and the
TypeScript type in `ts/index.ts` are spelled the same way.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["ALLOW", "DENY"]
Reason = Literal[
    "allowed",
    "denied by policy",
    "session revoked / delegator inactive",
    "invalid token",
]


class GuardrailFlag(BaseModel):
    flagged: bool = False
    categories: list[str] = Field(default_factory=list)


class DecisionEvent(BaseModel):
    id: str
    ts: str  # ISO-8601, UTC
    sid: str
    agent: str
    actingFor: str
    office: str
    tool: str
    resource: str | None = None
    decision: Decision
    reason: Reason
    policyIds: list[str] = Field(default_factory=list)
    atlas: list[str] = Field(default_factory=list)
    guardrail: GuardrailFlag = Field(default_factory=GuardrailFlag)
