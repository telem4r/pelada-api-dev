from __future__ import annotations

import logging
import time
from threading import RLock


from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import get_current_user, get_current_user_id
from app.services.auth_service import login_user, refresh_user_tokens, register_user
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.core.rate_limit import consume_rate_limit, consume_rate_limit_key
from app.schemas.foundation import FoundationSessionModel
from app.services.foundation_identity_service import FoundationIdentityService

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("app.auth")

foundation_identity_service = FoundationIdentityService()

_FAILED_LOGIN_LOCK = RLock()
_FAILED_LOGIN_STATE: dict[str, dict[str, float | int]] = {}


def _email_login_key(email: str) -> str:
    return (email or '').strip().lower()


def _ensure_email_not_locked(email: str) -> None:
    key = _email_login_key(email)
    now = time.time()
    with _FAILED_LOGIN_LOCK:
        state = _FAILED_LOGIN_STATE.get(key)
        if not state:
            return
        locked_until = float(state.get('locked_until', 0) or 0)
        if locked_until > now:
            retry_after = max(int(locked_until - now), 1)
            raise HTTPException(status_code=429, detail={
                'code': 'account_temporarily_locked',
                'message': 'Muitas tentativas de login para este email. Aguarde alguns instantes e tente novamente.',
                'details': {'retry_after_seconds': retry_after},
            })
        if float(state.get('window_until', 0) or 0) <= now:
            _FAILED_LOGIN_STATE.pop(key, None)


def _register_failed_login(email: str) -> None:
    key = _email_login_key(email)
    now = time.time()
    with _FAILED_LOGIN_LOCK:
        state = _FAILED_LOGIN_STATE.get(key)
        if not state or float(state.get('window_until', 0) or 0) <= now:
            state = {'count': 0, 'window_until': now + 900, 'locked_until': 0}
            _FAILED_LOGIN_STATE[key] = state
        state['count'] = int(state.get('count', 0) or 0) + 1
        count = int(state['count'])
        lock_seconds = 0
        if count >= 10:
            lock_seconds = 300
        elif count >= 5:
            lock_seconds = 30
        if lock_seconds > 0:
            state['locked_until'] = now + lock_seconds


def _clear_failed_login(email: str) -> None:
    key = _email_login_key(email)
    with _FAILED_LOGIN_LOCK:
        _FAILED_LOGIN_STATE.pop(key, None)



class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=256)
    accepted_terms: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


__all__ = [
    "router",
    "get_current_user",
    "get_current_user_id",
    "get_db",
    "RegisterRequest",
    "LoginRequest",
    "RefreshRequest",
    "TokenResponse",
]


@router.post("/register", response_model=TokenResponse)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    consume_rate_limit(request, scope="auth:register", limit=10, window_seconds=60)
    if not payload.accepted_terms:
        raise HTTPException(status_code=400, detail='É obrigatório aceitar os Termos de Uso e a Política de Privacidade.')
    return TokenResponse(**register_user(
        db,
        name=payload.name,
        email=payload.email,
        password=payload.password,
        accepted_terms=payload.accepted_terms,
    ))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    consume_rate_limit(request, scope="auth:login", limit=12, window_seconds=60)
    normalized_email = payload.email.strip().lower()
    _ensure_email_not_locked(normalized_email)
    consume_rate_limit_key(key=f"auth:login:email:{normalized_email}", limit=5, window_seconds=900)
    try:
        response = TokenResponse(**login_user(db, email=normalized_email, password=payload.password))
        _clear_failed_login(normalized_email)
        return response
    except HTTPException as exc:
        if exc.status_code == 401:
            _register_failed_login(normalized_email)
        raise


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    consume_rate_limit(request, scope="auth:refresh", limit=30, window_seconds=60)
    return TokenResponse(**refresh_user_tokens(db, refresh_token=payload.refresh_token))


@router.post("/social-login", response_model=FoundationSessionModel)
def social_login(
    request: Request,
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db),
):
    session = foundation_identity_service.bootstrap_session(db, principal)
    logger.info(
        'social_login_success request_id=%s provider=%s user_id=%s email=%s',
        getattr(getattr(request, 'state', None), 'request_id', None),
        principal.raw_claims.get('app_metadata', {}).get('provider'),
        principal.user_id,
        principal.email,
    )
    return session
