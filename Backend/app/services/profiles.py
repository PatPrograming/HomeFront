from supabase import Client

from app.schemas.profile import ProfileUpdate

TABLE = "profiles"


def get_profile(db: Client, user_id: str) -> dict | None:
    res = db.table(TABLE).select("*").eq("id", user_id).limit(1).execute()
    return res.data[0] if res.data else None


def update_profile(db: Client, user_id: str, changes: ProfileUpdate) -> dict | None:
    payload = changes.model_dump(exclude_unset=True)
    if not payload:
        return get_profile(db, user_id)
    res = db.table(TABLE).update(payload).eq("id", user_id).execute()
    return res.data[0] if res.data else None
