from __future__ import annotations

from datetime import timedelta

from app.core.time import utc_today
from app.db import get_session_local
from app.models import Group, GroupFinancialEntry
from app.services.notification_service import notify_if_allowed


def run() -> int:
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        tomorrow = utc_today() + timedelta(days=1)
        rows = db.query(GroupFinancialEntry).filter(
            GroupFinancialEntry.entry_type == "monthly",
            GroupFinancialEntry.due_date == tomorrow,
            GroupFinancialEntry.status.notin_(("paid", "cancelled", "forgiven")),
            GroupFinancialEntry.user_id.isnot(None),
        ).all()
        created = 0
        for row in rows:
            group = db.query(Group).filter(Group.id == row.group_id).first()
            if notify_if_allowed(
                db,
                user_id=row.user_id,
                type="monthly_due_tomorrow",
                title="Mensalidade vence amanhã",
                message=f"A mensalidade do grupo {group.name if group else row.group_id} vence amanhã.",
                payload={"group_id": row.group_id, "entry_id": row.id},
            ):
                created += 1
        db.commit()
        return created
    finally:
        db.close()
