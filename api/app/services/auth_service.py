from __future__ import annotations

import os
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import User
from app.player_utils import ensure_player_for_user
from app.repositories.users import get_user_by_email, list_valid_refresh_candidates
from app.core.time import utc_now
from app.security import AuthError, create_access_token, hash_password, password_needs_rehash, verify_password

REFRESH_DAYS = int(os.getenv('JWT_REFRESH_TTL_DAYS', '30'))
REFRESH_CANDIDATES_LIMIT = int(os.getenv('JWT_REFRESH_CANDIDATES_LIMIT', '200'))


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hash_password(token)


def issue_tokens_for_user(db: Session, user: User):
    try:
        ensure_player_for_user(db, user.id, user.name)
        access = create_access_token(user.id)
        refresh_plain = new_refresh_token()
        user.refresh_token_hash = hash_refresh_token(refresh_plain)
        user.refresh_token_expires_at = utc_now() + timedelta(days=REFRESH_DAYS)
        user.refresh_token = refresh_plain
        db.add(user)
        db.commit()
        return {'access_token': access, 'refresh_token': refresh_plain, 'token_type': 'bearer'}
    except AuthError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail='Não foi possível iniciar a sessão agora.')


def register_user(db: Session, *, name: str, email: str, password: str, accepted_terms: bool = False):
    email = email.strip().lower()
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail='Informe o seu nome para continuar.')
    if get_user_by_email(db, email=email):
        raise HTTPException(status_code=400, detail='Este email já está registado.')
    try:
        now = utc_now()
        user = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            terms_accepted_at=now if accepted_terms else None,
            privacy_accepted_at=now if accepted_terms else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail='Não foi possível criar a sua conta agora.')
    return issue_tokens_for_user(db, user)


def login_user(db: Session, *, email: str, password: str):
    email = email.strip().lower()
    user = get_user_by_email(db, email=email)
    if not user or not verify_password(password, getattr(user, 'password_hash', None)):
        raise HTTPException(status_code=401, detail='Email ou palavra-passe inválidos.')
    try:
        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
            db.add(user)
            db.commit()
            db.refresh(user)
    except Exception:
        db.rollback()
    return issue_tokens_for_user(db, user)


def refresh_user_tokens(db: Session, *, refresh_token: str):
    token = (refresh_token or '').strip()
    if not token:
        raise HTTPException(status_code=401, detail='A sua sessão expirou. Entre novamente para continuar.')
    now = utc_now()
    user = db.query(User).filter(User.refresh_token == token).first()
    if not user:
        for candidate in list_valid_refresh_candidates(db, now=now, limit=REFRESH_CANDIDATES_LIMIT):
            if verify_password(token, candidate.refresh_token_hash):
                user = candidate
                break
    if not user:
        raise HTTPException(status_code=401, detail='A sua sessão expirou. Entre novamente para continuar.')
    if not user.refresh_token_expires_at or user.refresh_token_expires_at <= now:
        raise HTTPException(status_code=401, detail='A sua sessão expirou. Entre novamente para continuar.')
    ensure_player_for_user(db, user.id, user.name)
    try:
        access = create_access_token(user.id)
        refresh_plain = new_refresh_token()
        user.refresh_token_hash = hash_refresh_token(refresh_plain)
        user.refresh_token_expires_at = now + timedelta(days=REFRESH_DAYS)
        user.refresh_token = refresh_plain
        db.add(user)
        db.commit()
        return {'access_token': access, 'refresh_token': refresh_plain, 'token_type': 'bearer'}
    except AuthError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail='Não foi possível atualizar a sua sessão agora.')
