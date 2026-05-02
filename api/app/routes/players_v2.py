"""Players V2 routes — list players for the authenticated user."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.core.supabase_storage import resolve_avatar_fields

router = APIRouter(prefix="/v2/players", tags=["Players V2"])


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class PlayerOut(BaseModel):
    id: str
    user_id: str
    name: str
    display_name: str
    position: str | None = None
    preferred_foot: str | None = None
    avatar_url: str | None = None
    rating: int = 0
    team_id: str | None = None
    is_active: bool = True


@router.get("", response_model=List[PlayerOut])
def list_my_players(principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                    db: Session = Depends(get_db_session)):
    rows = db.execute(text("""
        select p.id::text, p.user_id::text,
               coalesce(nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), 'Jogador') as name,
               coalesce(nullif(trim(p.display_name), ''), 'Jogador') as display_name,
               coalesce(p.primary_position, p.position) as position,
               p.preferred_foot, p.avatar_url, coalesce(p.rating, 0)::int as rating,
               p.team_id::text, p.is_active
        from public.players p
        where p.user_id = cast(:uid as uuid)
        order by p.created_at asc
    """), {'uid': principal.user_id}).mappings().all()
    return [PlayerOut(**resolve_avatar_fields(dict(r))) for r in rows]


@router.post("", response_model=PlayerOut, status_code=201)
def create_player(payload: dict,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    name = payload.get('name', 'Jogador')
    position = payload.get('position')
    rating = payload.get('rating', 0)
    team_id = payload.get('team_id')
    row = db.execute(text("""
        insert into public.players (id, user_id, display_name, primary_position, rating, team_id, is_active, is_public, created_at, updated_at)
        values (gen_random_uuid(), cast(:uid as uuid), :name, :pos, :rating,
                case when :tid is not null then cast(:tid as uuid) end,
                true, true, now(), now())
        returning id::text, user_id::text, display_name as name, display_name,
                  primary_position as position, preferred_foot, avatar_url,
                  coalesce(rating, 0)::int as rating, team_id::text, is_active
    """), {'uid': principal.user_id, 'name': name, 'pos': position, 'rating': rating, 'tid': team_id}).mappings().first()
    db.commit()
    return PlayerOut(**resolve_avatar_fields(dict(row)))
