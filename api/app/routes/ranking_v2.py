from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.ranking_v2 import RankingGroupResponseV2Model
from app.services.ranking_v2_service import RankingV2Service

router = APIRouter(prefix='/v2/groups/{group_id}/ranking', tags=['Ranking V2'])
service = RankingV2Service()


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get('', response_model=RankingGroupResponseV2Model)
def get_group_ranking(group_id: str, period: str = Query('all'), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_group_ranking(db, principal, group_id, period)
