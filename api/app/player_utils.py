from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Player, User


def _resolve_display_name(user: User, fallback_name: str | None = None) -> str:
    value = (
        getattr(user, 'name', None)
        or getattr(user, 'first_name', None)
        or fallback_name
        or 'Jogador'
    )
    value = str(value).strip()
    return value or 'Jogador'


def _resolve_full_name(user: User, fallback_name: str | None = None) -> str | None:
    name = (getattr(user, 'name', None) or fallback_name or '').strip()
    if name:
        return name
    first_name = (getattr(user, 'first_name', None) or '').strip()
    last_name = (getattr(user, 'last_name', None) or '').strip()
    full = f"{first_name} {last_name}".strip()
    return full or None


def ensure_player_for_user(db: Session, user_id: str, user_name: str | None = None) -> Player:
    """
    Garante que todo User tenha exatamente 1 Player vinculado ao schema atual.
    Compatível com o backend UUID/Supabase.
    """
    player = db.query(Player).filter(Player.user_id == user_id).first()
    if player:
        return player

    user = db.query(User).filter(User.id == user_id).first()
    display_name = _resolve_display_name(user, user_name) if user else (str(user_name).strip() if user_name else 'Jogador')
    full_name = _resolve_full_name(user, user_name) if user else (str(user_name).strip() or None if user_name else None)

    player = Player(
        user_id=user_id,
        display_name=display_name,
        full_name=full_name,
        rating=0,
        is_public=True,
        is_active=True,
    )

    db.add(player)
    db.commit()
    db.refresh(player)
    return player
