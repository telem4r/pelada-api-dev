from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.home_v2 import HomeSummaryV2Model
from app.services.home_v2_service import HomeV2Service

router = APIRouter(prefix='/v2/home', tags=['Home V2'])
service = HomeV2Service()


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get('/summary', response_model=HomeSummaryV2Model)
def get_home_summary(
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.get_summary(db, principal)
