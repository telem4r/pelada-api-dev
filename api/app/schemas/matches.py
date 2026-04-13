from __future__ import annotations

from pydantic import BaseModel


class MatchReminderPayload(BaseModel):
    match_id: int
