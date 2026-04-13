import os
from fastapi import APIRouter
from sqlalchemy import text

from app.db import get_session_local

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    # DB check + alembic version
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        version = db.execute(text("select version_num from alembic_version")).scalar()
    finally:
        db.close()

    return {
        "ok": True,
        "alembic_version": version,
        "jwt_secret_set": bool(os.getenv("JWT_SECRET")),
        "env": os.getenv("ENV", "unknown"),
    }
