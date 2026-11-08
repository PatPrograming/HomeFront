"""Admin "People & roles": who works here, which office role each person has, and what each role may do.

Every route is admin only, checked twice on the server: the Cognito access token must be in the `admin`
group (`require_admin`), and the caller's STAFFER item in DynamoDB must still say active admin, so a
demoted or offboarded admin is cut off at once rather than when their token expires.

A role's tool list is data in DynamoDB (`ROLE#<role>`). The gateway reads it on every tool call and Cedar
enforces it (`principal.roleTools`), so a change applies on the next call, even mid-session. The Cedar
policies themselves never change here. Every change writes an admin audit event.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from identity_gateway.gateway import get_gateway
from identity_gateway.storage import MandateStore
from mandate_contracts import TOOLS
from mandate_contracts.ddb import AdminEventItem, RoleItem, StafferItem
from mandate_contracts.roles import (
    LOCKED_TOOLS,
    ROLE_GOVERNED_TOOLS,
    ROLE_LABELS,
    ROLE_TOOLS,
    RoleChangeError,
    assignable_tools,
    describe_role_change,
    role_label,
    tool_action,
    validate_role_tools,
)

from app.services.directory import get_directory

try:
    from app.core.cognito import StaffIdentity, require_admin
except ModuleNotFoundError as exc:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import StaffIdentity, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def require_people_admin(admin: StaffIdentity = Depends(require_admin)) -> StaffIdentity:
    me = get_gateway().store.get_staffer(admin.username)
    if me is None or not me.active or me.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin only")
    return admin


class AssignIn(BaseModel):
    """What an admin may change about a person. Anything else in the body is a 422."""

    model_config = ConfigDict(extra="forbid")
    officeRole: str | None = None
    group: Literal["staffer", "admin"] | None = None


class RoleToolsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[str]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(store: MandateStore, admin: StaffIdentity, type_: str, target: str, before, after, summary: str) -> None:
    store.put_admin_event(
        AdminEventItem(
            id=uuid.uuid4().hex[:12], ts=_now(), actor=admin.username, type=type_, target=target,
            before=before, after=after, summary=summary,
        )
    )


def _people(store: MandateStore) -> list[dict]:
    staffers = store.list_staffers()
    users = {u.username: u for u in get_directory().list_users()}
    people = []
    for name in sorted(set(users) | set(staffers)):
        user, item = users.get(name), staffers.get(name)
        people.append(
            {
                "username": name,
                "email": user.email if user else None,
                "inDirectory": user is not None,
                "enabled": user.enabled if user else False,
                "group": user.group if user else None,  # Cognito: who may use the admin pages
                "officeRole": item.office_role if item else None,
                "roleLabel": role_label(item.office_role if item else None),
                "office": item.office if item else None,
                "active": item.active if item else False,
            }
        )
    return people


def _role_view(store: MandateStore, role: str, people: list[dict]) -> dict:
    item = store.get_role_item(role)
    return {
        "id": role,
        "label": ROLE_LABELS[role],
        "tools": sorted(store.get_role_tools(role), key=list(TOOLS).index),
        "defaultTools": list(ROLE_TOOLS[role]),
        "assignable": list(assignable_tools(role)),
        "customized": item is not None,
        "updatedBy": item.updated_by if item else None,
        "updatedAt": item.updated_at if item else None,
        "people": [p["username"] for p in people if p["officeRole"] == role],
    }


def _validated(role: str, tools: list[str]) -> tuple[str, ...]:
    if role not in ROLE_LABELS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"There is no office role called {role!r}.")
    try:
        return validate_role_tools(role, tools)
    except RoleChangeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


@router.get("/people")
def list_people(admin: StaffIdentity = Depends(require_people_admin)) -> dict:
    return {"people": _people(get_gateway().store)}


@router.get("/roles")
def list_roles(admin: StaffIdentity = Depends(require_people_admin)) -> dict:
    store = get_gateway().store
    people = _people(store)
    return {
        "roles": [_role_view(store, role, people) for role in ROLE_LABELS],
        "tools": [
            {
                "name": name,
                "action": tool_action(name),
                "alwaysDenied": spec.always_denied,
                # Roles a locked tool is locked to (lead ruling: only they may ever hold it); [] when not locked.
                "lockedTo": [ROLE_LABELS[r] for r in ROLE_LABELS if r in LOCKED_TOOLS.get(name, ())],
            }
            for name, spec in TOOLS.items()
        ],
    }


def _change(store: MandateStore, role: str, tools: tuple[str, ...]) -> dict:
    before = store.get_role_tools(role)
    affects = [p["username"] for p in _people(store) if p["officeRole"] == role]
    return {
        "role": role,
        "tools": list(tools),
        "changes": describe_role_change(role, before, tools),
        "affects": affects,
        "before": [t for t in ROLE_GOVERNED_TOOLS if t in before],
    }


@router.post("/roles/{role}/preview")
def preview_role_tools(role: str, body: RoleToolsIn, admin: StaffIdentity = Depends(require_people_admin)) -> dict:
    """What saving this list would let (or stop) the role's staff and their assistants do. Changes nothing."""
    return _change(get_gateway().store, role, _validated(role, body.tools))


@router.put("/roles/{role}")
def set_role_tools(role: str, body: RoleToolsIn, admin: StaffIdentity = Depends(require_people_admin)) -> dict:
    store = get_gateway().store
    tools = _validated(role, body.tools)
    change = _change(store, role, tools)
    store.put_role_tools(role, RoleItem(tools=list(tools), updated_by=admin.username, updated_at=_now()))
    summary = " ".join(change["changes"]) or f"{ROLE_LABELS[role]} saved with no change."
    _audit(store, admin, "role_tools_changed", role, change["before"], list(tools), summary)
    return change


@router.put("/people/{username}")
def assign(username: str, body: AssignIn, admin: StaffIdentity = Depends(require_people_admin)) -> dict:
    if body.officeRole is None and body.group is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to change.")
    if body.officeRole is not None and body.officeRole not in ROLE_LABELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"There is no office role called {body.officeRole!r}.")
    store = get_gateway().store
    directory = get_directory()
    users = {u.username: u for u in directory.list_users()}
    item = store.get_staffer(username)
    if username not in users and item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown person")

    current_group = users[username].group if username in users else None
    if body.group is not None and body.group != current_group:
        if username not in users:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "This person has no login, so they have no group")
        if current_group == "admin":
            others = [u for u in users.values() if u.group == "admin" and u.enabled and u.username != username]
            if not others:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "This is the last admin. Make someone else an admin first."
                )
        directory.set_group(username, body.group)
        if item is not None:
            store.set_staffer_group(username, body.group)
        _audit(
            store, admin, "group_changed", username, current_group, body.group,
            f"{username} moved from {current_group or 'no group'} to {body.group}.",
        )

    if body.officeRole is not None:
        before = item.office_role if item else None
        if item is None:
            me = store.get_staffer(admin.username)  # checked in require_people_admin
            group = body.group or current_group or "staffer"
            store.put_staffer(
                username, StafferItem(active=True, office=me.office, role=group, office_role=body.officeRole)
            )
        else:
            store.set_office_role(username, body.officeRole)
        _audit(
            store, admin, "role_assigned", username, before, body.officeRole,
            f"{username} is now {role_label(body.officeRole)} (was {role_label(before)}). "
            "Their assistant's next tool call uses the new role.",
        )

    return next(p for p in _people(store) if p["username"] == username)


@router.get("/audit")
def admin_audit(admin: StaffIdentity = Depends(require_people_admin)) -> dict:
    return {"events": [e.model_dump(mode="json", by_alias=True) for e in get_gateway().store.admin_events()]}
