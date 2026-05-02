from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Player, User


def get_profile_user(db: Session, *, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def get_primary_player(db: Session, *, user_id: int):
    return db.query(Player).filter(Player.owner_id == user_id).order_by(Player.id.asc()).first()


def apply_profile_update(user: User, data: dict):
    updatable = {
        'avatar_url', 'first_name', 'last_name', 'birth_date', 'favorite_team',
        'birth_country', 'birth_state', 'birth_city', 'current_country', 'current_state',
        'current_city', 'position', 'preferred_foot', 'language'
    }
    for key, value in data.items():
        if key in updatable:
            setattr(user, key, value)
    if ('first_name' in data) or ('last_name' in data):
        fn = ((user.first_name or '').strip() if getattr(user, 'first_name', None) is not None else '')
        ln = ((user.last_name or '').strip() if getattr(user, 'last_name', None) is not None else '')
        full = (fn + ' ' + ln).strip()
        if full:
            user.name = full


def update_profile(db: Session, *, user: User, data: dict):
    apply_profile_update(user, data)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
