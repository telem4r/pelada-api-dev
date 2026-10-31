from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Annotated
from urllib.request import Request, urlopen

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwk, jwt
from jose.exceptions import JWKError

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


class SupabaseAuthUnavailable(SupabaseAuthError):
    """Authentication could not be verified; access must remain denied."""


_jwks_cache: dict[str, Any] | None = None
_jwks_cache_expires_at: float = 0.0
_jwks_next_refresh_at: float = 0.0
_jwks_lock = Lock()

# Bound revocation delay and network work caused by arbitrary, unknown key IDs.
_JWKS_TTL_SECONDS = 600
_JWKS_REFRESH_COOLDOWN_SECONDS = 30
_JWKS_MAX_BYTES = 256 * 1024
_SUPPORTED_ALGORITHMS = frozenset({"ES256", "RS256", "HS256"})
_INVALID_TOKEN = "Token de acesso inválido ou expirado"
_AUTH_UNAVAILABLE = "Não foi possível verificar a autenticação. Tente novamente em instantes."


def _jwks_url() -> str:
    if not settings.supabase_url:
        raise SupabaseAuthUnavailable(_AUTH_UNAVAILABLE)
    return f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


def _download_jwks() -> dict[str, Any]:
    # Only the configured project is trusted; token jku/x5u headers are ignored.
    req = Request(_jwks_url(), headers={"Accept": "application/json"})
    with urlopen(req, timeout=5) as response:
        raw = response.read(_JWKS_MAX_BYTES + 1)
    if len(raw) > _JWKS_MAX_BYTES:
        raise ValueError("JWKS response exceeds size limit")
    return json.loads(raw.decode("utf-8"))


def _valid_jwks_shape(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("keys"), list)
        and all(isinstance(key, dict) for key in payload["keys"])
    )


def _fresh_cached_jwks(now: float) -> dict[str, Any] | None:
    if _jwks_cache is not None and now < _jwks_cache_expires_at:
        return _jwks_cache
    return None


def get_jwks(force_refresh: bool = False) -> dict[str, Any] | None:
    """Return trusted keys for at most ten minutes, never an expired cache.

    A shared lock and cooldown bound refreshes during rotation, outages and
    requests with random key IDs. A failed refresh may reuse a still-valid
    cache, but never extends its lifetime. An empty key set replaces old keys.
    """
    global _jwks_cache, _jwks_cache_expires_at, _jwks_next_refresh_at
    cached = _fresh_cached_jwks(time.monotonic())
    if cached is not None and not force_refresh:
        return cached
    with _jwks_lock:
        now = time.monotonic()
        cached = _fresh_cached_jwks(now)
        if cached is not None and not force_refresh:
            return cached
        if now < _jwks_next_refresh_at:
            return cached
        _jwks_next_refresh_at = now + _JWKS_REFRESH_COOLDOWN_SECONDS
        try:
            downloaded = _download_jwks()
            if not _valid_jwks_shape(downloaded):
                raise ValueError("Invalid JWKS response")
        except Exception as exc:
            # Do not log credentials, JWTs, claims or the response body.
            logger.warning("supabase_jwks_fetch_failed error_type=%s", type(exc).__name__)
            return _fresh_cached_jwks(time.monotonic())
        _jwks_cache = downloaded
        _jwks_cache_expires_at = time.monotonic() + _JWKS_TTL_SECONDS
        return downloaded


def _get_jwt_secret() -> str | None:
    # A service_role API key is NOT a signing secret. Keep JWT_SECRET support
    # for existing deployments, with an explicit Supabase-only override.
    return settings.supabase_jwt_secret or settings.jwt_secret or None


def _select_public_key(jwks: dict[str, Any], *, kid: str, alg: str) -> dict[str, Any] | None:
    expected_type = "EC" if alg == "ES256" else "RSA"
    matches = []
    for key in jwks["keys"]:
        if key.get("kid") != kid or key.get("kty") != expected_type:
            continue
        if key.get("alg", alg) != alg or key.get("use", "sig") != "sig":
            continue
        operations = key.get("key_ops")
        if operations is not None and (not isinstance(operations, list) or "verify" not in operations):
            continue
        if alg == "ES256" and key.get("crv") != "P-256":
            continue
        matches.append(key)
    # Ambiguous IDs must not select an arbitrary trusted key.
    return matches[0] if len(matches) == 1 else None


def _verify_token(token: str, key: Any, *, alg: str, issuer: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=settings.supabase_jwt_audience,
            issuer=issuer,
            options={
                "verify_signature": True,
                "verify_at_hash": False,  # API access token, not an OIDC ID token.
                "require_iss": True,
                "require_aud": True,
                "require_sub": True,
                "require_exp": True,
            },
        )
        # python-jose coerces NumericDate values to int. Reject malformed and
        # non-finite values explicitly, and enforce the exact expiry boundary.
        for name in ("exp", "nbf", "iat"):
            value = payload.get(name)
            if name in payload and (type(value) not in (int, float) or not math.isfinite(value)):
                raise SupabaseAuthError(_INVALID_TOKEN)
        if payload["exp"] <= time.time():
            raise SupabaseAuthError(_INVALID_TOKEN)
        if "nbf" in payload and payload["nbf"] > time.time():
            raise SupabaseAuthError(_INVALID_TOKEN)
        if not isinstance(payload["sub"], str) or not payload["sub"].strip():
            raise SupabaseAuthError(_INVALID_TOKEN)
        return payload
    except JWKError as exc:
        raise SupabaseAuthUnavailable(_AUTH_UNAVAILABLE) from exc
    except (JWTError, TypeError, ValueError, OverflowError) as exc:
        raise SupabaseAuthError(_INVALID_TOKEN) from exc


def _decode_token(token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except (JWTError, TypeError, ValueError) as exc:
        raise SupabaseAuthError(_INVALID_TOKEN) from exc
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in _SUPPORTED_ALGORITHMS:
        raise SupabaseAuthError(_INVALID_TOKEN)
    if header.get("crit") or "b64" in header:
        raise SupabaseAuthError(_INVALID_TOKEN)
    if not settings.supabase_url or not settings.supabase_jwt_audience:
        raise SupabaseAuthUnavailable(_AUTH_UNAVAILABLE)
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"

    if alg == "HS256":
        secret = _get_jwt_secret()
        if not secret:
            raise SupabaseAuthUnavailable(_AUTH_UNAVAILABLE)
        return _verify_token(token, secret, alg=alg, issuer=issuer)

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        raise SupabaseAuthError(_INVALID_TOKEN)
    jwks = get_jwks()
    if jwks is None:
        raise SupabaseAuthUnavailable(_AUTH_UNAVAILABLE)
    key = _select_public_key(jwks, kid=kid, alg=alg)
    if key is None:
        # Rotation publishes a new kid. Never refresh just because signature
        # or claims validation failed for an already-known key.
        refreshed = get_jwks(force_refresh=True)
        if refreshed is not None:
            key = _select_public_key(refreshed, kid=kid, alg=alg)
    if key is None:
        raise SupabaseAuthError(_INVALID_TOKEN)
    try:
        key = jwk.construct(key, alg)
    except (JWKError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise SupabaseAuthUnavailable(_AUTH_UNAVAILABLE) from exc
    return _verify_token(token, key, alg=alg, issuer=issuer)


def _principal_from_payload(payload: dict[str, Any]) -> SupabasePrincipal:
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise SupabaseAuthError(_INVALID_TOKEN)
    email = payload.get("email")
    return SupabasePrincipal(user_id=user_id, email=email, raw_claims=payload)


def get_current_supabase_principal(
    token: Annotated[str, Depends(SUPABASE_OAUTH2)]
) -> SupabasePrincipal:
    try:
        payload = _decode_token(token)
        return _principal_from_payload(payload)
    except SupabaseAuthUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_AUTH_UNAVAILABLE,
            headers={"Retry-After": str(_JWKS_REFRESH_COOLDOWN_SECONDS)},
        ) from exc
    except SupabaseAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
