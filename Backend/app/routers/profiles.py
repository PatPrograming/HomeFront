import httpx
from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials

from app.core.cognito import StaffIdentity, bearer, require_staff
from app.core.config import get_settings
from app.schemas.profile import Me

router = APIRouter(prefix="/me", tags=["profiles"])

USERINFO_TIMEOUT_SECONDS = 5
_email_by_sub: dict[str, str] = {}  # Cognito access tokens carry no email, so we ask userInfo once per user


def _userinfo_email(sub: str, token: str) -> str | None:
    """The user's email from Cognito's userInfo endpoint, or None if it cannot be had."""
    if sub in _email_by_sub:
        return _email_by_sub[sub]
    domain = get_settings().cognito_domain.removeprefix("https://")  # infra prints it with the scheme
    if not domain:
        return None
    try:
        response = httpx.get(
            f"https://{domain}/oauth2/userInfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=USERINFO_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        email = response.json().get("email")
    except (httpx.HTTPError, ValueError):  # the profile still loads without an email
        return None
    if email:
        _email_by_sub[sub] = email
    return email


@router.get("", response_model=Me)
def read_me(
    identity: StaffIdentity = Depends(require_staff),
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> Me:
    return Me(
        username=identity.username,
        sub=identity.sub,
        email=identity.email or _userinfo_email(identity.sub, creds.credentials),
        groups=sorted(identity.groups),
    )
