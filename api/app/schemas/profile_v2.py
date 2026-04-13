from __future__ import annotations

from datetime import date
from pydantic import BaseModel, Field


class ProfileV2Out(BaseModel):
    id: str
    player_id_str: str | None = None
    name: str
    email: str | None = None
    avatar_url: str | None = None

    first_name: str | None = None
    last_name: str | None = None
    birth_date: date | None = None
    favorite_team: str | None = None

    birth_country: str | None = None
    birth_state: str | None = None
    birth_city: str | None = None

    current_country: str | None = None
    current_state: str | None = None
    current_city: str | None = None

    position: str | None = None
    preferred_foot: str | None = None
    language: str | None = None


class ProfileV2UpdateIn(BaseModel):
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    birth_date: date | None = None
    favorite_team: str | None = Field(default=None, max_length=120)

    birth_country: str | None = Field(default=None, max_length=100)
    birth_state: str | None = Field(default=None, max_length=100)
    birth_city: str | None = Field(default=None, max_length=120)

    current_country: str | None = Field(default=None, max_length=100)
    current_state: str | None = Field(default=None, max_length=100)
    current_city: str | None = Field(default=None, max_length=120)

    position: str | None = Field(default=None, max_length=80)
    preferred_foot: str | None = Field(default=None, max_length=30)
    language: str | None = Field(default=None, max_length=10)
