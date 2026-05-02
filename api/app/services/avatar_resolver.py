from __future__ import annotations

from typing import Any

from app.core.supabase_storage import resolve_avatar_fields, resolve_avatar_url


def resolve_avatar(value: str | None) -> str | None:
    try:
        return resolve_avatar_url(value)
    except Exception:
        return None


def resolve_avatars(payload: Any) -> Any:
    try:
        return resolve_avatar_fields(payload)
    except Exception:
        return payload
