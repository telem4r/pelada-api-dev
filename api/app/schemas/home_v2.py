from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HomeProfileV2Model(BaseModel):
    user_id: str
    player_id: str | None = None
    name: str
    email: str | None = None
    avatar_url: str | None = None
    position: str | None = None
    city: str | None = None
    reputation_score: float | None = None
    reputation_label: str | None = None


class HomeGroupV2Model(BaseModel):
    id: str
    name: str
    avatar_url: str | None = None
    group_type: str | None = None
    role: str | None = None
    member_status: str | None = None
    members_count: int = 0


class HomeMatchV2Model(BaseModel):
    id: str
    group_id: str
    group_name: str | None = None
    title: str | None = None
    status: str | None = None
    starts_at: datetime
    ends_at: datetime | None = None
    location_name: str | None = None
    city: str | None = None
    confirmed_count: int = 0
    waiting_count: int = 0
    guests_count: int = 0
    arrived_count: int = 0
    is_current_user_confirmed: bool = False
    draw_status: str | None = None


class HomeNotificationsV2Model(BaseModel):
    unread_count: int = 0
    latest_created_at: datetime | None = None


class HomeSummaryV2Model(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    profile: HomeProfileV2Model
    groups: list[HomeGroupV2Model] = Field(default_factory=list)
    next_match: HomeMatchV2Model | None = None
    upcoming_matches: list[HomeMatchV2Model] = Field(default_factory=list)
    notifications: HomeNotificationsV2Model = Field(default_factory=HomeNotificationsV2Model)
    flags: dict[str, Any] = Field(default_factory=dict)
