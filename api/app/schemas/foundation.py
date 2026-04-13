from __future__ import annotations

from pydantic import BaseModel


class FoundationUserModel(BaseModel):
    id: str
    email: str | None = None


class FoundationPlayerModel(BaseModel):
    id: str
    user_id: str
    display_name: str
    full_name: str | None = None
    nickname: str | None = None
    primary_position: str | None = None
    secondary_position: str | None = None
    avatar_url: str | None = None
    is_public: bool = True
    is_active: bool = True


class FoundationSessionModel(BaseModel):
    user: FoundationUserModel
    player: FoundationPlayerModel
    source: str = 'supabase_bootstrap'
