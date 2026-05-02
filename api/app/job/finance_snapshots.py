from __future__ import annotations

from datetime import date

from app.db import get_session_local
from app.models import Group
from app.services.finance_snapshot_service import rebuild_snapshot


def run(reference_month: date) -> int:
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        groups = db.query(Group).all()
        count = 0
        for group in groups:
            rebuild_snapshot(db, group_id=group.id, reference_month=reference_month)
            count += 1
        db.commit()
        return count
    finally:
        db.close()
