from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

Base = declarative_base()

_engine = None
_session_factory = None


def get_database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return ""
    if "sslmode=" not in url:
        url = url + ("&" if "?" in url else "?") + "sslmode=require"
    return url


def get_engine():
    global _engine
    if _engine is None:
        db_url = get_database_url()
        if not db_url:
            raise RuntimeError("DATABASE_URL não configurado")
        _engine = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


def get_session_local():
    """Retorna a sessionmaker factory (NÃO uma sessão instanciada)."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
        )
    return _session_factory


def get_db():
    """FastAPI dependency — yields a Session and closes it after."""
    factory = get_session_local()
    db = factory()
    try:
        yield db
    finally:
        db.close()
