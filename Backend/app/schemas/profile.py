import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


FormKind = Literal["emails", "social"]
MAX_FORM_JSON_CHARS = 100_000


class FormSubmission(BaseModel):
    """Answers from one of the outreach forms (Emails / Social Platform)."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any]

    @field_validator("data")
    @classmethod
    def _bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(v, default=str)) > MAX_FORM_JSON_CHARS:
            raise ValueError("Form data is too large")
        return v


class FormSaved(BaseModel):
    kind: FormKind
    saved_at: datetime
