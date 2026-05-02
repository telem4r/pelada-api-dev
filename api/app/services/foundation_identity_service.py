from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.core.supabase_storage import resolve_avatar_url
from app.repositories.foundation_identity import FoundationIdentityRepository
from app.schemas.foundation import FoundationPlayerModel, FoundationSessionModel, FoundationUserModel

logger = logging.getLogger("app.foundation_identity")


class FoundationIdentityService:
    def __init__(self, repository: FoundationIdentityRepository | None = None) -> None:
        self.repository = repository or FoundationIdentityRepository()

    def _metadata(self, principal: SupabasePrincipal) -> dict:
        value = principal.raw_claims.get('user_metadata')
        return value if isinstance(value, dict) else {}

    def _display_name(self, principal: SupabasePrincipal) -> str:
        metadata = self._metadata(principal)
        return (
            metadata.get('display_name')
            or metadata.get('full_name')
            or metadata.get('name')
            or (principal.email.split('@', 1)[0] if principal.email else None)
            or 'Jogador'
        )

    def bootstrap_session(self, db: Session, principal: SupabasePrincipal) -> FoundationSessionModel:
        metadata = self._metadata(principal)
        outcome = self.repository.bootstrap_user_and_player(
            db,
            user_id=principal.user_id,
            email=principal.email,
            display_name=self._display_name(principal),
            full_name=metadata.get('full_name'),
            nickname=metadata.get('nickname'),
        )
        db.commit()
        logger.info(
            'foundation_bootstrap user_id=%s email=%s link_action=%s linked_from_user_id=%s',
            principal.user_id,
            principal.email,
            outcome.get('link_action'),
            outcome.get('linked_from_user_id'),
        )
        return self.get_session(db, principal)

    def get_session(self, db: Session, principal: SupabasePrincipal) -> FoundationSessionModel:
        payload = self.repository.fetch_session_identity(db, user_id=principal.user_id, email=principal.email)
        if not payload:
            raise HTTPException(status_code=404, detail='Sessão BoraFut ainda não bootstrapada para este utilizador.')
        return FoundationSessionModel(
            user=FoundationUserModel(
                id=payload['user_id'],
                email=payload['user_email'],
            ),
            player=FoundationPlayerModel(
                id=payload['player_id'],
                user_id=payload['player_user_id'],
                display_name=payload['display_name'],
                full_name=payload['full_name'],
                nickname=payload['nickname'],
                primary_position=payload['primary_position'],
                secondary_position=payload['secondary_position'],
                avatar_url=resolve_avatar_url(payload['avatar_url']),
                is_public=bool(payload['is_public']),
                is_active=bool(payload['is_active']),
            ),
        )
