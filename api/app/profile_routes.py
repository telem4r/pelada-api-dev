from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import get_current_user as get_current_user_id  # ✅ retorna int (user_id)
from app.avatars_routes import resolve_avatar_url
from app.services.profile_service import get_primary_player, get_profile_user, update_profile

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileOut(BaseModel):
    id: int
    player_id: Optional[int] = None
    name: str
    email: Optional[str] = None
    avatar_url: Optional[str] = None

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    birth_date: Optional[date] = None
    favorite_team: Optional[str] = None

    birth_country: Optional[str] = None
    birth_state: Optional[str] = None
    birth_city: Optional[str] = None

    current_country: Optional[str] = None
    current_state: Optional[str] = None
    current_city: Optional[str] = None

    # ✅ Mantemos como string para evitar inconsistência com enums entre versões
    position: Optional[str] = None
    preferred_foot: Optional[str] = None

    language: Optional[str] = None


class ProfileUpdateIn(BaseModel):
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)
    birth_date: Optional[date] = None
    favorite_team: Optional[str] = Field(default=None, max_length=120)

    birth_country: Optional[str] = Field(default=None, max_length=100)
    birth_state: Optional[str] = Field(default=None, max_length=100)
    birth_city: Optional[str] = Field(default=None, max_length=120)

    current_country: Optional[str] = Field(default=None, max_length=100)
    current_state: Optional[str] = Field(default=None, max_length=100)
    current_city: Optional[str] = Field(default=None, max_length=120)

    position: Optional[str] = Field(default=None, max_length=80)
    preferred_foot: Optional[str] = Field(default=None, max_length=30)

    language: Optional[str] = Field(default=None, max_length=10)


def _to_out(u: User, player_id: Optional[int] = None) -> ProfileOut:
    return ProfileOut(
        id=u.id,
        player_id=player_id,
        name=u.name,
        email=u.email,
        avatar_url=resolve_avatar_url(getattr(u, "avatar_url", None)),

        first_name=getattr(u, "first_name", None),
        last_name=getattr(u, "last_name", None),
        birth_date=getattr(u, "birth_date", None),
        favorite_team=getattr(u, "favorite_team", None),

        birth_country=getattr(u, "birth_country", None),
        birth_state=getattr(u, "birth_state", None),
        birth_city=getattr(u, "birth_city", None),

        current_country=getattr(u, "current_country", None),
        current_state=getattr(u, "current_state", None),
        current_city=getattr(u, "current_city", None),

        position=getattr(u, "position", None),
        preferred_foot=getattr(u, "preferred_foot", None),

        language=getattr(u, "language", None),
    )


@router.get("/me", response_model=ProfileOut)
def get_my_profile(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    u = get_profile_user(db, user_id=user_id)
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    player = get_primary_player(db, user_id=user_id)
    return _to_out(u, player_id=player.id if player else None)


@router.put("/me", response_model=ProfileOut)
def update_my_profile(
    payload: ProfileUpdateIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    u = get_profile_user(db, user_id=user_id)
    if not u:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")

    data = payload.model_dump(exclude_unset=True)
    if not data:
        player = get_primary_player(db, user_id=user_id)
        return _to_out(u, player_id=player.id if player else None)

    u = update_profile(db, user=u, data=data)
    player = get_primary_player(db, user_id=user_id)
    return _to_out(u, player_id=player.id if player else None)
