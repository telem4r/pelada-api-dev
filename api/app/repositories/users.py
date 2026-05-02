from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import User


def get_user_or_none(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def get_user_by_email(db: Session, *, email: str):
    normalized = (email or '').strip().lower()
    if not normalized:
        return None
    return db.query(User).filter(func.lower(User.email) == normalized).first()


def list_valid_refresh_candidates(db: Session, *, now: datetime, limit: int):
    return (
        db.query(User)
        .filter(User.refresh_token_hash.isnot(None))
        .filter(User.refresh_token_expires_at.isnot(None))
        .filter(User.refresh_token_expires_at > now)
        .order_by(User.refresh_token_expires_at.asc())
        .limit(limit)
        .all()
    )
