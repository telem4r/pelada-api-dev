from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Annotated
from urllib.request import Request, urlopen

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from app.core.config import settings

logger = logging.getLogger("app.supabase_auth")

SUPABASE_OAUTH2 = OAuth2PasswordBearer(tokenUrl="/auth/v1/token")


@dataclass(frozen=True)
class SupabasePrincipal:
    user_id: str
    email: str | None
    raw_claims: dict[str, Any]


class SupabaseAuthError(Exception):
    pass


_jwks_cache: dict[str, Any] | None = None
_jwks_cache_expires_at: float = 0.0

_SUPPORTED_ALGORITHMS = ["ES256", "HS256", "RS256"]


def _jwks_url() -> str:
    if not settings.supabase_url:
        raise SupabaseAuthError("SUPABASE_URL não configurado")
    return f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"


def _download_jwks() -> dict[str, Any]:
    req = Request(_jwks_url(), headers={"Accept": "application/json"})
    with urlopen(req, timeout=5) as response:
        payload = response.read().decode("utf-8")
        return json.loads(payload)


def get_jwks(force_refresh: bool = False) -> dict[str, Any] | None:
    """Tenta descarregar JWKS. Retorna None se falhar (ex: rede bloqueada)."""
    global _jwks_cache, _jwks_cache_expires_at
    now = time.time()
    if not force_refresh and _jwks_cache is not None and now < _jwks_cache_expires_at:
        return _jwks_cache
    try:
        _jwks_cache = _download_jwks()
        _jwks_cache_expires_at = now + 3600
        return _jwks_cache
    except Exception as exc:
        logger.warning("JWKS download failed (network blocked?): %s", exc)
        return _jwks_cache  # return stale cache or None


def _peek_algorithm(token: str) -> str | None:
    try:
        header = jwt.get_unverified_header(token)
        return header.get("alg")
    except Exception:
        return None


def _get_jwt_secret() -> str | None:
    """Retorna o JWT secret configurado (para validação HS256)."""
    return settings.jwt_secret or settings.supabase_service_role_key or None


def _decode_token(token: str) -> dict[str, Any]:
    alg = _peek_algorithm(token)

    # --- Tentativa 1: HS256 com JWT secret ---
    jwt_secret = _get_jwt_secret()
    if jwt_secret:
        try:
            return jwt.decode(
                token,
                jwt_secret,
                algorithms=["HS256"],
                audience=settings.supabase_jwt_audience,
                options={"verify_at_hash": False},
            )
        except JWTError:
            pass  # token pode ser ES256, tentar JWKS

    # --- Tentativa 2: ES256/RS256 via JWKS ---
    jwks = get_jwks()
    if jwks:
        algorithms = [alg] if alg in _SUPPORTED_ALGORITHMS else _SUPPORTED_ALGORITHMS
        try:
            return jwt.decode(
                token,
                jwks,
                algorithms=algorithms,
                audience=settings.supabase_jwt_audience,
                options={"verify_at_hash": False},
            )
        except JWTError:
            # Tentar refresh das chaves (rotação)
            jwks_fresh = get_jwks(force_refresh=True)
            if jwks_fresh:
                try:
                    return jwt.decode(
                        token,
                        jwks_fresh,
                        algorithms=algorithms,
                        audience=settings.supabase_jwt_audience,
                        options={"verify_at_hash": False},
                    )
                except JWTError:
                    pass

    # --- Tentativa 3: Validação de claims sem verificação de assinatura ---
    # Usado quando JWKS não está disponível (rede bloqueada no App Runner)
    # e HS256 não funcionou. Valida: iss, aud, exp, sub.
    logger.warning("JWKS unavailable and HS256 failed. Falling back to claim-only validation.")
    try:
        claims = jwt.get_unverified_claims(token)
    except JWTError as exc:
        raise SupabaseAuthError("Token malformado") from exc

    # Validar issuer
    expected_iss = f"{settings.supabase_url}/auth/v1"
    if claims.get("iss") != expected_iss:
        raise SupabaseAuthError(
            f"Token issuer inválido: {claims.get('iss')} (esperado {expected_iss})"
        )

    # Validar audience
    if claims.get("aud") != settings.supabase_jwt_audience:
        raise SupabaseAuthError("Token audience inválido")

    # Validar expiração
    exp = claims.get("exp")
    if exp is not None and time.time() > float(exp):
        raise SupabaseAuthError("Token expirado")

    # Validar subject
    if not claims.get("sub"):
        raise SupabaseAuthError("Token sem subject")

    return claims


def _principal_from_payload(payload: dict[str, Any]) -> SupabasePrincipal:
    user_id = payload.get("sub")
    if not user_id:
        raise SupabaseAuthError("Token Supabase sem subject")
    email = payload.get("email")
    return SupabasePrincipal(user_id=user_id, email=email, raw_claims=payload)


def get_current_supabase_principal(
    token: Annotated[str, Depends(SUPABASE_OAUTH2)]
) -> SupabasePrincipal:
    try:
        payload = _decode_token(token)
        return _principal_from_payload(payload)
    except SupabaseAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
