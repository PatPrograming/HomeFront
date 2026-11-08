from pydantic import BaseModel


class Me(BaseModel):
    """Who the caller is, straight from their Cognito access token."""

    username: str
    sub: str
    email: str | None = None
    groups: list[str]
