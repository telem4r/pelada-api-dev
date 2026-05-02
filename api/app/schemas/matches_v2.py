from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class MatchCreateV2Request(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    starts_at: datetime
    ends_at: datetime
    location_name: Optional[str] = Field(None, max_length=160)
    city: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = Field(None, max_length=1000)
    line_slots: int = Field(..., ge=0, le=99)
    goalkeeper_slots: int = Field(..., ge=0, le=20)
    price_cents: Optional[int] = Field(None, ge=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=10)
    payment_method: Optional[str] = Field(None, max_length=40)
    payment_key: Optional[str] = Field(None, max_length=255)
    single_waitlist_release_days: Optional[int] = Field(0, ge=0, le=30)
    modality: Optional[str] = Field(None, max_length=50)
    gender_type: Optional[str] = Field(None, max_length=30)
    is_public: bool = False


class MatchUpdateV2Request(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    location_name: Optional[str] = Field(None, max_length=160)
    city: Optional[str] = Field(None, max_length=120)
    notes: Optional[str] = Field(None, max_length=1000)
    line_slots: Optional[int] = Field(None, ge=0, le=99)
    goalkeeper_slots: Optional[int] = Field(None, ge=0, le=20)
    price_cents: Optional[int] = Field(None, ge=0)
    currency: Optional[str] = Field(None, min_length=3, max_length=10)
    payment_method: Optional[str] = Field(None, max_length=40)
    payment_key: Optional[str] = Field(None, max_length=255)
    single_waitlist_release_days: Optional[int] = Field(None, ge=0, le=30)
    modality: Optional[str] = Field(None, max_length=50)
    gender_type: Optional[str] = Field(None, max_length=30)
    is_public: Optional[bool] = None
    roster_locked: Optional[bool] = None
    draw_locked: Optional[bool] = None

class MatchSummaryV2Model(BaseModel):
    id: str
    group_id: str
    created_by_user_id: str
    title: Optional[str] = None
    status: str = 'scheduled'
    starts_at: datetime
    ends_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    location_name: Optional[str] = None
    city: Optional[str] = None
    notes: Optional[str] = None
    line_slots: int = 0
    goalkeeper_slots: int = 0
    confirmed_count: int = 0
    waiting_count: int = 0
    guests_count: int = 0
    arrived_count: int = 0
    is_current_user_confirmed: bool = False
    draw_status: str = 'pending'
    value_per_player: float = 0
    price_cents: Optional[int] = None
    currency: Optional[str] = None
    payment_method: Optional[str] = None
    payment_key: Optional[str] = None
    single_waitlist_release_days: int = 0
    modality: Optional[str] = None
    gender_type: Optional[str] = None
    is_public: bool = False
    roster_locked: bool = False
    draw_locked: bool = False


class MatchParticipantV2Model(BaseModel):
    participant_id: Optional[str] = None
    player_id: Optional[str] = None
    user_id: Optional[str] = None
    guest_id: Optional[str] = None
    kind: str
    name: str
    avatar_url: Optional[str] = None
    position: str
    status: str
    queue_order: int = 0
    is_paid: bool = False
    has_arrived: bool = False
    approved_by_user_id: Optional[str] = None
    approved_by_user_name: Optional[str] = None
    requires_approval: bool = False
    can_play_draw: bool = False
    billing_type: Optional[str] = None


class MatchPresenceV2Model(BaseModel):
    match_id: str
    line_slots: int
    goalkeeper_slots: int
    confirmed: list[MatchParticipantV2Model]
    waiting: list[MatchParticipantV2Model]
    confirmed_line_count: int = 0
    confirmed_goalkeeper_count: int = 0
    waiting_line_count: int = 0
    waiting_goalkeeper_count: int = 0
    arrived_count: int = 0
    draw_eligible_count: int = 0


class MatchPresenceUpsertV2Request(BaseModel):
    position: str = Field(..., pattern='^(linha|goleiro)$')

    @field_validator('position', mode='before')
    @classmethod
    def normalize_position(cls, value: str) -> str:
        raw = str(value or '').strip().lower()
        if raw in {'line', 'linha'}:
            return 'linha'
        if raw in {'goalkeeper', 'goleiro', 'gol'}:
            return 'goleiro'
        return raw


class MatchGuestCreateV2Request(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    position: str = Field(..., pattern='^(linha|goleiro)$')
    status: Optional[str] = Field('auto', pattern='^(auto|confirmado|espera)$')
    skill_rating: Optional[int] = Field(3, ge=1, le=5)

    @field_validator('position', mode='before')
    @classmethod
    def normalize_position(cls, value: str) -> str:
        raw = str(value or '').strip().lower()
        if raw in {'line', 'linha'}:
            return 'linha'
        if raw in {'goalkeeper', 'goleiro', 'gol'}:
            return 'goleiro'
        return raw


class MatchGuestV2Model(BaseModel):
    guest_id: str
    match_id: str
    name: str
    position: str
    status: str
    queue_order: int = 0
    has_arrived: bool = False
    is_paid: bool = False
    skill_rating: Optional[int] = None


class MatchDrawBaseItemV2Model(BaseModel):
    entry_id: str
    kind: str
    name: str
    position: str
    can_play_draw: bool = True


class MatchDrawBaseV2Model(BaseModel):
    match_id: str
    total_confirmed: int = 0
    eligible_count: int = 0
    players: list[MatchDrawBaseItemV2Model]


class MatchDrawGenerateV2Request(BaseModel):
    players_per_team: int = Field(..., ge=2, le=30, alias='playersPerTeam')
    team_count: int = Field(2, ge=2, le=8)

    @model_validator(mode='before')
    @classmethod
    def normalize_payload(cls, values):
        data = dict(values or {})
        if data.get('players_per_team') is None and data.get('playersPerTeam') is None:
            legacy_players_per_team = data.get('team_size') or data.get('players')
            if legacy_players_per_team is not None:
                data['players_per_team'] = legacy_players_per_team
        return data


class MatchOperationLocksV2Request(BaseModel):
    roster_locked: Optional[bool] = None
    draw_locked: Optional[bool] = None


class MatchDrawTeamItemV2Model(BaseModel):
    entry_id: str
    kind: str
    participant_id: Optional[str] = None
    guest_id: Optional[str] = None
    player_id: Optional[str] = None
    name: str
    position: str
    has_arrived: bool = True
    can_view_skill: bool = False
    skill_rating: Optional[int] = None


class MatchDrawTeamV2Model(BaseModel):
    team_number: int
    players: list[MatchDrawTeamItemV2Model]
    line_count: int = 0
    goalkeeper_count: int = 0
    skill_total: Optional[int] = None
    skill_average: Optional[float] = None
    can_view_metrics: bool = False


class MatchDrawResultV2Model(BaseModel):
    match_id: str
    draw_id: str
    team_count: int
    generated_at: datetime
    generated_by_user_id: str
    eligible_count: int = 0
    players_per_team: Optional[int] = None
    can_view_skill: bool = False
    skill_visibility: str = 'hidden_for_member'
    teams: list[MatchDrawTeamV2Model]


class MatchEventCreateV2Request(BaseModel):
    entry_id: str
    kind: str = Field(..., pattern='^(member|guest)$')
    event_type: str = Field(..., pattern='^(goal|assist|own_goal|yellow_card|red_card)$')
    minute: Optional[int] = Field(None, ge=0, le=200)
    notes: Optional[str] = Field(None, max_length=255)


class MatchEventV2Model(BaseModel):
    event_id: str
    match_id: str
    team_number: int
    entry_id: str
    kind: str
    participant_id: Optional[str] = None
    guest_id: Optional[str] = None
    player_id: Optional[str] = None
    display_name: str
    position: str
    event_type: str
    minute: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime


class MatchScoreTeamV2Model(BaseModel):
    team_number: int
    goals: int = 0


class MatchGameFlowV2Model(BaseModel):
    match_id: str
    status: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    scoreboard: list[MatchScoreTeamV2Model]
    events: list[MatchEventV2Model]


class MatchPlayerStatV2Model(BaseModel):
    entry_id: str
    kind: str
    team_number: int
    participant_id: Optional[str] = None
    guest_id: Optional[str] = None
    player_id: Optional[str] = None
    display_name: str
    position: str
    goals: int = 0
    assists: int = 0
    own_goals: int = 0
    yellow_cards: int = 0
    red_cards: int = 0


class MatchStatsSummaryV2Model(BaseModel):
    match_id: str
    status: str
    is_consolidated: bool = False
    manual_submitted: bool = False
    totals: dict[str, int]
    players: list[MatchPlayerStatV2Model]


class MatchPostPlayerStatsV2Item(BaseModel):
    player_id: str
    goals: int = Field(0, ge=0, le=99)
    assists: int = Field(0, ge=0, le=99)
    wins: int = Field(0, ge=0, le=99)
    fair_play: int = Field(0, ge=0, le=5)
    mvp: bool = False


class MatchPostStatsV2Request(BaseModel):
    players: list[MatchPostPlayerStatsV2Item] = Field(default_factory=list)
