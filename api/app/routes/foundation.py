from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.foundation import FoundationSessionModel
from app.services.foundation_identity_service import FoundationIdentityService

router = APIRouter(prefix="/v2", tags=["Foundation"])
service = FoundationIdentityService()


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get("/health")
def foundation_health():
    db_ok = False
    db_error = None
    if settings.database_url:
        SessionLocal = get_session_local()
        try:
            with SessionLocal() as session:
                session.execute(text("select 1"))
                db_ok = True
        except Exception as exc:  # pragma: no cover - runtime guard
            db_error = str(exc)
    return {
        "status": "ok",
        "supabase": {
            "url_configured": bool(settings.supabase_url),
            "anon_key_configured": bool(settings.supabase_anon_key),
            "service_role_configured": bool(settings.supabase_service_role_key),
            "jwt_audience": settings.supabase_jwt_audience,
        },
        "database": {
            "configured": bool(settings.database_url),
            "reachable": db_ok,
            "error": db_error,
        },
    }


@router.post("/bootstrap/me", response_model=FoundationSessionModel)
def foundation_bootstrap_me(
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.bootstrap_session(db, principal)


@router.get("/me", response_model=FoundationSessionModel)
def foundation_me(
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.get_session(db, principal)
