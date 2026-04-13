from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field

class SocialProfileV2Model(BaseModel):
    player_id: str
    user_id: str | None = None
    display_name: str
    position: str | None = None
    city: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    skill_level: int | None = None
    birth_city: str | None = None
    birth_state: str | None = None
    birth_country: str | None = None
    birth_date: str | None = None
    preferred_foot: str | None = None
    ranking_score: int = 0
    matches_played: int = 0
    goals: int = 0
    assists: int = 0

class SocialFollowRequestV2Model(BaseModel):
    player_id: str = Field(min_length=1)

class SocialFollowV2Model(BaseModel):
    id: str
    target_player_id: str
    target_display_name: str
    avatar_url: str | None = None
    position: str | None = None
    city: str | None = None
    followed_at: datetime

class SocialFeedItemV2Model(BaseModel):
    id: str
    event_type: str
    title: str
    description: str
    occurred_at: datetime
    actor_player_id: str | None = None
    actor_display_name: str | None = None
    actor_avatar_url: str | None = None
    group_id: str | None = None
    group_name: str | None = None
    match_id: str | None = None

class SocialFeedResponseV2Model(BaseModel):
    items: list[SocialFeedItemV2Model]

class SocialSearchResponseV2Model(BaseModel):
    items: list[SocialProfileV2Model]

class SocialFollowingResponseV2Model(BaseModel):
    items: list[SocialFollowV2Model]
