from __future__ import annotations
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.social_v2 import SocialFeedResponseV2Model, SocialFollowRequestV2Model, SocialFollowV2Model, SocialFollowingResponseV2Model, SocialProfileV2Model, SocialSearchResponseV2Model
from app.services.social_v2_service import SocialV2Service

router = APIRouter(prefix='/v2/social', tags=['Social V2'])
service = SocialV2Service()

def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@router.get('/profile/me', response_model=SocialProfileV2Model)
def get_my_profile(principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_my_profile(db, principal)

@router.get('/profile/{player_id}', response_model=SocialProfileV2Model)
def get_public_profile(player_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_public_profile(db, principal, player_id)

@router.get('/search', response_model=SocialSearchResponseV2Model)
def search_players(q: str = Query(..., min_length=2), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.search_players(db, principal, q)

@router.get('/following', response_model=SocialFollowingResponseV2Model)
def list_following(principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_following(db, principal)

@router.post('/following', response_model=SocialFollowV2Model)
def follow_player(payload: SocialFollowRequestV2Model, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.follow(db, principal, payload.player_id)

@router.delete('/following/{player_id}')
def unfollow_player(player_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.unfollow(db, principal, player_id)

@router.get('/feed', response_model=SocialFeedResponseV2Model)
def get_feed(principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.feed(db, principal)
