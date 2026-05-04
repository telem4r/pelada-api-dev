from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HomeSummaryV2Model(BaseModel):
    profile: dict[str, Any] | None = None
    social_profile: dict[str, Any] | None = None
    reputation: dict[str, Any] | None = None
    next_confirmed_match: dict[str, Any] | None = None
    upcoming_week: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str
