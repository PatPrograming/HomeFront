"""STUB — replace with lane A's `Backend/app/core/cognito.py` (WP1).

Same names and shapes as the Cognito module in docs/LANES.md. Until lane A
merges, every dependency here refuses with 401, so the broker and admin routes
fail closed. Tests replace them with `app.dependency_overrides`. The routers
import `app.core.cognito` first and only fall back to this file when it does
not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status


@dataclass(frozen=True)
class StaffIdentity:
    username: str
    sub: str
    email: str | None
    groups: frozenset[str]


@dataclass(frozen=True)
class AgentClientIdentity:
    client_id: str
    scopes: frozenset[str]


def _refuse() -> None:
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Cognito verification is not installed yet")


def require_staff() -> StaffIdentity:
    _refuse()


def require_admin(staff: StaffIdentity = Depends(require_staff)) -> StaffIdentity:
    # Same contract as lane A: 401 without a valid login, 403 for a logged-in non-admin.
    if "admin" not in staff.groups:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin group required")
    return staff


def require_agent_client() -> AgentClientIdentity:
    _refuse()
