from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.core.supabase_storage import resolve_avatar_fields
from app.repositories.profile_v2 import ProfileV2Repository
from app.schemas.profile_v2 import ProfileV2Out


class ProfileV2Service:
    def __init__(self, repository: ProfileV2Repository | None = None) -> None:
        self.repository = repository or ProfileV2Repository()

    def get_me(self, db: Session, principal: SupabasePrincipal) -> ProfileV2Out:
        item = self.repository.fetch_me(db, user_id=principal.user_id)
        if not item:
            raise HTTPException(status_code=404, detail='Perfil do utilizador não encontrado.')
        return ProfileV2Out(**resolve_avatar_fields(item))

    def update_me(self, db: Session, principal: SupabasePrincipal, data: dict) -> ProfileV2Out:
        self.repository.update_me(db, user_id=principal.user_id, data=data)
        db.commit()
        return self.get_me(db, principal)

    def get_reputation(self, db: Session, principal: SupabasePrincipal, player_id: str) -> dict:
        # force auth/me existence
        if not self.repository.fetch_me(db, user_id=principal.user_id):
            raise HTTPException(status_code=404, detail='Perfil do utilizador não encontrado.')
        item = self.repository.get_reputation(db, player_id=player_id)
        if not item:
            raise HTTPException(status_code=404, detail='Jogador não encontrado.')
        return item
