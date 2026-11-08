"""Mandate token claims (brief §6.2).

The broker mints this token after checking the staffer's Cognito login and the
agent's Cognito client credentials. `sub` is the agent, `act` is the staffer it
acts for, `sid` is the DynamoDB session the gateway checks on every call.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ISSUER = "mandate-broker"
SCOPE = "mandate/tools"
TTL_SECONDS = 15 * 60


class MandateClaims(BaseModel):
    iss: Literal["mandate-broker"] = ISSUER
    sub: str  # "agent-<id>"
    act: str  # staffer user id
    office: str  # e.g. "rep-x"
    mode: Literal["official", "campaign"] = "official"
    sid: str  # session uuid
    scope: list[str] = [SCOPE]
    iat: int
    exp: int
