from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    id: str
    username: str | None = None
    full_name: str | None = None
    avatar_url: str | None = None
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, min_length=3, max_length=32)
    full_name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = None
