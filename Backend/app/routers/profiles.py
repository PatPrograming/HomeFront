from fastapi import APIRouter, Depends

from app.core.cognito import StaffIdentity, require_staff
from app.schemas.profile import Me

router = APIRouter(prefix="/me", tags=["profiles"])


@router.get("", response_model=Me)
def read_me(identity: StaffIdentity = Depends(require_staff)) -> Me:
    return Me(
        username=identity.username,
        sub=identity.sub,
        email=identity.email,
        groups=sorted(identity.groups),
    )
