from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import get_current_user  # ✅ retorna user_id (int)
from app.models import Player, User

router = APIRouter(prefix="/players", tags=["players"])


class PlayerOut(BaseModel):
    id: int
    owner_id: int
    name: str
    rating: int
    position: Optional[str] = None
    preferred_foot: Optional[str] = None
    team_id: Optional[int] = None

    class Config:
        from_attributes = True


def _ensure_default_player(db: Session, user_id: int) -> Player:
    """
    Regra do sistema: todo User => Player automático.
    Se não existir, cria 1 player default com rating=3.
    """
    existing = (
        db.query(Player)
        .filter(Player.owner_id == user_id)
        .order_by(Player.id.asc())
        .first()
    )
    if existing:
        # garante rating não nulo (segurança)
        if existing.rating is None:
            existing.rating = 3
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return existing

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    p = Player(
        owner_id=user_id,
        name=((user.name or "").strip() or "Jogador"),
        rating=3,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("", response_model=List[PlayerOut])
def list_my_players(
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    # garante que sempre exista pelo menos 1 player
    _ensure_default_player(db, current_user_id)

    players = (
        db.query(Player)
        .filter(Player.owner_id == current_user_id)
        .order_by(Player.id.asc())
        .all()
    )
    return players
