"""Connected accounts (Gmail, X): list, connect, disconnect. The staffer is always the token's username."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from identity_gateway.connections import (
    PROVIDERS,
    MissingPermission,
    NotConfigured,
    OAuthStateError,
    ProviderError,
    get_connections,
)

from app.core.cognito import StaffIdentity, require_staff

router = APIRouter(prefix="/connections", tags=["connections"])


class Callback(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


def _known_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider")


@router.get("")
def list_connections(identity: StaffIdentity = Depends(require_staff)) -> dict:
    return {"connections": get_connections().list(identity.username)}


@router.post("/callback")
def finish_connection(body: Callback, identity: StaffIdentity = Depends(require_staff)) -> dict:
    service = get_connections()
    try:
        connection = service.finish(identity.username, body.code, body.state)
    except OAuthStateError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_state") from None
    except NotConfigured:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_set_up") from None
    except MissingPermission:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "missing_permission: you signed in but did not allow sending on your behalf. Connect again and tick every box.",
        ) from None
    except ProviderError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "provider_error") from None
    return next(entry for entry in service.list(identity.username) if entry["provider"] == connection.provider)


@router.post("/{provider}/start")
def start_connection(provider: str, identity: StaffIdentity = Depends(require_staff)) -> dict:
    _known_provider(provider)
    try:
        return {"authorizeUrl": get_connections().start(identity.username, provider)}
    except NotConfigured:
        raise HTTPException(status.HTTP_409_CONFLICT, "not_set_up") from None


@router.delete("/{provider}")
def disconnect(provider: str, identity: StaffIdentity = Depends(require_staff)) -> dict:
    _known_provider(provider)
    get_connections().disconnect(identity.username, provider)
    return {"provider": provider, "connected": False}
