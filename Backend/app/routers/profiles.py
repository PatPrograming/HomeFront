from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import CurrentUser, get_current_user
from app.schemas.profile import Profile, ProfileUpdate
from app.services import profiles as service

router = APIRouter(prefix="/me", tags=["profiles"])


@router.get("", response_model=Profile)
def read_me(user: CurrentUser = Depends(get_current_user)):
    # First call after sign-up creates the profile (was a Postgres trigger).
    return service.get_or_create_profile(user)


@router.patch("", response_model=Profile)
def update_me(changes: ProfileUpdate, user: CurrentUser = Depends(get_current_user)):
    try:
        return service.update_profile(user, changes)
    except service.UsernameTaken:
        raise HTTPException(409, "Username is already taken")
    except service.ProfileConflict:
        raise HTTPException(409, "Profile changed concurrently, please retry")
