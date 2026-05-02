import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

REFRESH_DAYS = int(os.getenv("JWT_REFRESH_TTL_DAYS", "30"))

def new_refresh_token() -> str:
    # 48 bytes ~ 64 chars urlsafe
    return secrets.token_urlsafe(48)

def hash_refresh_token(token: str) -> str:
    # hash rápido (não precisa KDF porque token é aleatório forte)
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def refresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=REFRESH_DAYS)
