"""Live feed (WP2): the dashboard polls decision events every second."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from identity_gateway.gateway import get_gateway

try:
    from app.core.cognito import StaffIdentity, require_staff
except ModuleNotFoundError as exc:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import StaffIdentity, require_staff

router = APIRouter(tags=["events"])


@router.get("/events")
def list_events(
    since: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    staff: StaffIdentity = Depends(require_staff),
) -> dict:
    """`since` is the `next` cursor from the previous answer (an event sort key `<ts>#<id>`)."""
    if since is not None:
        try:
            cursor_time = datetime.fromisoformat(since.split("#", 1)[0])
            # A cursor from the future would hide every new event.
            if cursor_time > datetime.now(timezone.utc) + timedelta(minutes=1):
                raise ValueError("cursor is in the future")
        except (ValueError, TypeError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "since must be a cursor from a previous answer")
    events, cursor = get_gateway().store.events_since(since, limit=limit)
    return {"events": [e.model_dump(mode="json") for e in events], "next": cursor}
