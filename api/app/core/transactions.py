from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


@contextmanager
def atomic(db: Session, *, detail: str = "Database transaction failed") -> Iterator[Session]:
    try:
        yield db
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=detail) from exc
    except Exception:
        db.rollback()
        raise


def commit_refresh(db: Session, entity, *, detail: str = "Database transaction failed"):
    with atomic(db, detail=detail):
        db.add(entity)
    db.refresh(entity)
    return entity
