"""Live feed (WP2): the dashboard polls decision events every second."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from identity_gateway.gateway import get_gateway

try:
    from app.core.cognito import StaffIdentity, require_staff
except ImportError:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
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
            datetime.fromisoformat(since.split("#", 1)[0])
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "since must be a cursor from a previous answer")
    events, cursor = get_gateway().store.events_since(since, limit=limit)
    return {"events": [e.model_dump(mode="json") for e in events], "next": cursor}
