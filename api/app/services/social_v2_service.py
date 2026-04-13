from __future__ import annotations
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.core.cache import app_cache
from app.core.supabase_auth import SupabasePrincipal
from app.repositories.social_v2 import SocialV2Repository
from app.schemas.social_v2 import SocialFeedItemV2Model, SocialFeedResponseV2Model, SocialFollowV2Model, SocialFollowingResponseV2Model, SocialProfileV2Model, SocialSearchResponseV2Model
from app.services.avatar_resolver import resolve_avatars

class SocialV2Service:
    def __init__(self, repository: SocialV2Repository | None = None) -> None:
        self.repository = repository or SocialV2Repository()

    def _me(self, db: Session, principal: SupabasePrincipal) -> dict:
        player = self.repository.fetch_my_player(db, user_id=principal.user_id)
        if not player:
            raise HTTPException(status_code=404, detail='Perfil do jogador não encontrado.')
        return player

    def get_my_profile(self, db: Session, principal: SupabasePrincipal) -> SocialProfileV2Model:
        me = self._me(db, principal)
        profile = self.repository.fetch_player_profile(db, player_id=me['player_id']) or me
        return SocialProfileV2Model(**resolve_avatars(profile))

    def get_public_profile(self, db: Session, principal: SupabasePrincipal, player_id: str) -> SocialProfileV2Model:
        self._me(db, principal)
        profile = self.repository.fetch_player_profile(db, player_id=player_id)
        if not profile:
            raise HTTPException(status_code=404, detail='Jogador não encontrado.')
        return SocialProfileV2Model(**resolve_avatars(profile))

    def search_players(self, db: Session, principal: SupabasePrincipal, query: str) -> SocialSearchResponseV2Model:
        me = self._me(db, principal)
        rows = self.repository.search_players(db, query=query, current_player_id=me['player_id'])
        return SocialSearchResponseV2Model(items=[SocialProfileV2Model(**resolve_avatars(row)) for row in rows])

    def follow(self, db: Session, principal: SupabasePrincipal, player_id: str) -> SocialFollowV2Model:
        me = self._me(db, principal)
        if me['player_id'] == player_id:
            raise HTTPException(status_code=400, detail='Você não pode seguir o próprio perfil.')
        target = self.repository.fetch_player_profile(db, player_id=player_id)
        if not target:
            raise HTTPException(status_code=404, detail='Jogador não encontrado.')
        data = self.repository.create_follow(db, follower_player_id=me['player_id'], followed_player_id=player_id)
        db.commit()
        app_cache.invalidate_prefix(f'social_v2:following:{me["player_id"]}')
        app_cache.invalidate_prefix(f'social_v2:feed:{me["player_id"]}')
        return SocialFollowV2Model(id=data['id'], target_player_id=player_id, target_display_name=target['display_name'], avatar_url=resolve_avatars(target).get('avatar_url'), position=target.get('position'), city=target.get('city'), followed_at=data['followed_at'])

    def unfollow(self, db: Session, principal: SupabasePrincipal, player_id: str) -> dict:
        me = self._me(db, principal)
        self.repository.delete_follow(db, follower_player_id=me['player_id'], followed_player_id=player_id)
        db.commit()
        app_cache.invalidate_prefix(f'social_v2:following:{me["player_id"]}')
        app_cache.invalidate_prefix(f'social_v2:feed:{me["player_id"]}')
        return {'ok': True, 'message': 'Perfil removido da sua lista.'}

    def list_following(self, db: Session, principal: SupabasePrincipal) -> SocialFollowingResponseV2Model:
        me = self._me(db, principal)
        key = f'social_v2:following:{me["player_id"]}'
        def _load():
            rows = self.repository.list_following(db, follower_player_id=me['player_id'])
            return SocialFollowingResponseV2Model(items=[SocialFollowV2Model(**resolve_avatars(row)) for row in rows])
        return app_cache.get_or_set(key, _load, ttl_seconds=20)

    def feed(self, db: Session, principal: SupabasePrincipal) -> SocialFeedResponseV2Model:
        me = self._me(db, principal)
        key = f'social_v2:feed:{me["player_id"]}'
        def _load():
            rows = self.repository.list_feed(db, follower_player_id=me['player_id'])
            return SocialFeedResponseV2Model(items=[SocialFeedItemV2Model(**resolve_avatars(row)) for row in rows])
        return app_cache.get_or_set(key, _load, ttl_seconds=20)
