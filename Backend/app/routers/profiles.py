from fastapi import APIRouter, Depends, HTTPException
from supabase import Client

from app.core.auth import CurrentUser, get_current_user
from app.core.supabase import get_user_client
from app.schemas.profile import Profile, ProfileUpdate
from app.services import profiles as service

router = APIRouter(prefix="/me", tags=["profiles"])


@router.get("", response_model=Profile)
def read_me(
    user: CurrentUser = Depends(get_current_user), db: Client = Depends(get_user_client)
):
    profile = service.get_profile(db, user.id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    return profile


@router.patch("", response_model=Profile)
def update_me(
    changes: ProfileUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Client = Depends(get_user_client),
):
    profile = service.update_profile(db, user.id, changes)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    return profile
