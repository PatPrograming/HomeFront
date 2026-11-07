"""Supabase client factories.

- user_client: anon key + the caller's JWT, so RLS applies. Use this by default.
- admin_client: service role key, which BYPASSES RLS. Admin and background jobs only.
"""
from functools import lru_cache

from fastapi import Depends
from supabase import Client, create_client

from app.core.auth import CurrentUser, get_current_user
from app.core.config import get_settings


def get_user_client(user: CurrentUser = Depends(get_current_user)) -> Client:
    settings = get_settings()
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(user.token)
    return client


@lru_cache
def get_admin_client() -> Client:
    settings = get_settings()
    if not settings.supabase_service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is not set")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)
