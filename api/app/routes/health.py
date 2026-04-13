from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.core.db import get_session_local

router = APIRouter(tags=["Health"])


@router.get("/health")
def health():
    base = {
        "version": settings.app_version,
        "env": settings.env,
        "database_url_set": bool(settings.database_url),
        "supabase_url_set": bool(settings.supabase_url),
    }
    if not settings.database_url:
        return {**base, "status": "no_database", "db_ok": False}
    try:
        SessionLocal = get_session_local()
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {**base, "status": "ok", "db_ok": True}
    except Exception:
        return {**base, "status": "degraded", "db_ok": False, "db_error": "unavailable"}
