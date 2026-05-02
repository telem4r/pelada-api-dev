"""Teams V2 routes — CRUD for teams using UUID-native tables."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal

router = APIRouter(prefix="/v2/teams", tags=["Teams V2"])


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class TeamOut(BaseModel):
    id: str
    name: str
    logo_url: Optional[str] = None
    created_at: Optional[str] = None


class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    logo_url: Optional[str] = None


@router.get("", response_model=List[TeamOut])
def list_teams(principal: SupabasePrincipal = Depends(get_current_supabase_principal),
               db: Session = Depends(get_db_session)):
    rows = db.execute(text("""
        select t.id::text, t.name, t.logo_url, t.created_at::text
        from public.teams t
        order by t.name asc
    """)).mappings().all()
    return [TeamOut(**dict(r)) for r in rows]


@router.post("", response_model=TeamOut, status_code=201)
def create_team(payload: TeamCreate,
                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                db: Session = Depends(get_db_session)):
    row = db.execute(text("""
        insert into public.teams (id, name, logo_url, created_at, updated_at)
        values (gen_random_uuid(), :name, :logo, now(), now())
        returning id::text, name, logo_url, created_at::text
    """), {'name': payload.name, 'logo': payload.logo_url}).mappings().first()
    db.commit()
    return TeamOut(**dict(row))


@router.get("/{team_id}", response_model=TeamOut)
def get_team(team_id: str,
             principal: SupabasePrincipal = Depends(get_current_supabase_principal),
             db: Session = Depends(get_db_session)):
    row = db.execute(text("""
        select id::text, name, logo_url, created_at::text from public.teams
        where id = cast(:tid as uuid) limit 1
    """), {'tid': team_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Time não encontrado.")
    return TeamOut(**dict(row))


@router.put("/{team_id}", response_model=TeamOut)
def update_team(team_id: str, payload: TeamCreate,
                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                db: Session = Depends(get_db_session)):
    row = db.execute(text("""
        update public.teams set name = :name, logo_url = :logo, updated_at = now()
        where id = cast(:tid as uuid)
        returning id::text, name, logo_url, created_at::text
    """), {'tid': team_id, 'name': payload.name, 'logo': payload.logo_url}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Time não encontrado.")
    db.commit()
    return TeamOut(**dict(row))


@router.delete("/{team_id}", status_code=204)
def delete_team(team_id: str,
                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                db: Session = Depends(get_db_session)):
    result = db.execute(text("delete from public.teams where id = cast(:tid as uuid)"), {'tid': team_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Time não encontrado.")
    db.commit()
