from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session

from app.communication_utils import create_notification, notification_allowed


def notify_if_allowed(db: Session, *, user_id: int, type: str, title: str, message: str, payload: dict[str, Any] | None = None, channel: str = "finance") -> bool:
    if not notification_allowed(db, user_id, channel):
        return False
    create_notification(db, user_id=user_id, type=type, title=title, message=message, payload=payload or {})
    return True
