"""Admin (WP2): offboard a staffer. Their agent's next tool call is denied."""

from fastapi import APIRouter, Depends, HTTPException, status

from identity_gateway.connections import get_connections
from identity_gateway.gateway import get_gateway

from app.services.agent_supervisor import get_supervisor

try:
    from app.core.cognito import StaffIdentity, require_admin
except ModuleNotFoundError as exc:  # STUB until lane A (WP1) merges Backend/app/core/cognito.py
    if exc.name != "app.core.cognito":
        raise
    from identity_gateway.auth_stub import StaffIdentity, require_admin

router = APIRouter(tags=["admin"])


@router.post("/staff/{staffer_id}/offboard")
def offboard(staffer_id: str, admin: StaffIdentity = Depends(require_admin)) -> dict:
    store = get_gateway().store
    if store.get_staffer(staffer_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown staffer")
    # Inactive staffer alone blocks every call (guarded() checks it each time);
    # revoking the sessions as well makes the audit trail say so explicitly.
    store.set_staffer_active(staffer_id, False)
    revoked = store.revoke_sessions_for(staffer_id)
    revoked_connections = get_connections().revoke_all(staffer_id)  # Gmail/X grants end with the job
    supervisor = get_supervisor()
    if supervisor:  # the agent would stop at its next call anyway; this ends the process now
        supervisor.stop_staffer(staffer_id, "staffer offboarded")
    return {"staffer": staffer_id, "active": False, "revokedSessions": revoked, "revokedConnections": revoked_connections}
