from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.profile_v2 import ProfileV2Out, ProfileV2UpdateIn
from app.services.profile_v2_service import ProfileV2Service

router = APIRouter(prefix='/v2', tags=['Profile V2'])
service = ProfileV2Service()


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get('/profile/me', response_model=ProfileV2Out)
def get_my_profile(principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_me(db, principal)


@router.put('/profile/me', response_model=ProfileV2Out)
def update_my_profile(payload: ProfileV2UpdateIn, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_me(db, principal, payload.model_dump(exclude_unset=True))


@router.get('/players/{player_id}/reputation')
def get_player_reputation(player_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_reputation(db, principal, player_id)
