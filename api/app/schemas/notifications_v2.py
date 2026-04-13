from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class NotificationV2Model(BaseModel):
    notification_id: str
    recipient_user_id: str
    group_id: str | None = None
    event_type: str
    title: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    is_read: bool = False
    created_at: datetime


class NotificationUnreadCountV2Model(BaseModel):
    unread_count: int


class NotificationRealtimeSnapshotV2Model(BaseModel):
    unread_count: int
    latest_created_at: datetime | None = None
    items: list[NotificationV2Model] = Field(default_factory=list)
