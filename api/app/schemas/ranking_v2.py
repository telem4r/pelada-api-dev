from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class RankingPlayerV2Model(BaseModel):
    player_id: str
    user_id: str | None = None
    display_name: str
    avatar_url: str | None = None
    games: int = 0
    goals: int = 0
    assists: int = 0
    own_goals: int = 0
    yellow_cards: int = 0
    red_cards: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    score: int = 0
    last_match_at: datetime | None = None


class RankingGroupResponseV2Model(BaseModel):
    group_id: str
    period: str = 'all'
    generated_at: datetime
    players: list[RankingPlayerV2Model]
