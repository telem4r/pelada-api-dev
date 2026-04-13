import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext

JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
JWT_ALG = "HS256"
ACCESS_MINUTES = int(os.getenv("JWT_ACCESS_TTL_MINUTES", "15"))

# ✅ melhor com barra inicial
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# ✅ Suporta hashes existentes no banco (pbkdf2_sha256) e o novo padrão (bcrypt)
# IMPORTANTÍSSIMO: o primeiro scheme é o padrão usado em hash_password().
pwd_context = CryptContext(
    schemes=["bcrypt", "pbkdf2_sha256"],
    deprecated="auto",
    pbkdf2_sha256__rounds=260000,
)


class AuthError(Exception):
    pass


def _require_secret():
    if not JWT_SECRET:
        raise AuthError("JWT_SECRET não configurado")


def hash_password(password: str) -> str:
    """
    Gera hash no padrão atual (bcrypt), mas aceita validar hashes legados (pbkdf2_sha256).
    """
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """
    ✅ Nunca lança exception:
    - se hash estiver null → False
    - se hash for de esquema desconhecido/corrompido → False
    """
    if not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except Exception:
        return False


def password_needs_rehash(password_hash: Optional[str]) -> bool:
    """
    Retorna True se o hash atual deve ser "upgraded" para o esquema padrão (bcrypt),
    por exemplo quando está em pbkdf2_sha256.
    """
    if not password_hash:
        return False
    try:
        return pwd_context.needs_update(password_hash)
    except Exception:
        return False


def create_access_token(user_id: int) -> str:
    _require_secret()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ACCESS_MINUTES)

    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        # ✅ exp como timestamp (int) — padrão mais compatível
        "exp": int(exp.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_access_token(token: str) -> int:
    _require_secret()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        sub = payload.get("sub")
        if not sub:
            raise AuthError("Token inválido (sub ausente)")
        return int(sub)
    except (JWTError, ValueError) as e:
        raise AuthError(f"Token inválido: {e}")


def get_current_user_id(token: Annotated[str, Depends(oauth2_scheme)]) -> int:
    """
    Helper explícito (retorna apenas o user_id).
    """
    try:
        return decode_access_token(token)
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )


# ✅ Mantém compatibilidade com o resto do projeto (muitos lugares importam get_current_user)
def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> int:
    return get_current_user_id(token)
