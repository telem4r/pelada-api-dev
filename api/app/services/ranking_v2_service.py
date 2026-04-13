from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.cache import app_cache
from app.core.supabase_auth import SupabasePrincipal
from app.repositories.ranking_v2 import RankingV2Repository
from app.schemas.ranking_v2 import RankingGroupResponseV2Model, RankingPlayerV2Model
from app.services.avatar_resolver import resolve_avatar


class RankingV2Service:
    def __init__(self, repository: RankingV2Repository | None = None) -> None:
        self.repository = repository or RankingV2Repository()

    def _require_active_membership(self, db: Session, *, group_id: str, user_id: str) -> dict:
        membership = self.repository.fetch_membership(db, group_id=group_id, user_id=user_id)
        if not membership or membership.get('status') != 'active':
            raise HTTPException(status_code=403, detail='Você ainda não é membro ativo deste grupo.')
        return membership

    @staticmethod
    def _parse_period(period: str | None) -> tuple[str, int | None]:
        normalized = (period or 'all').strip().lower()
        mapping = {'7d': 7, '30d': 30, 'all': None}
        if normalized not in mapping:
            raise HTTPException(status_code=422, detail='Período inválido. Use 7d, 30d ou all.')
        return normalized, mapping[normalized]

    @staticmethod
    def _score(row: dict) -> int:
        return int(row.get('games', 0)) * 3 + int(row.get('wins', 0)) * 5 + int(row.get('fair_play', 0)) * 2 + int(row.get('goals', 0)) + int(row.get('assists', 0))

    def get_group_ranking(self, db: Session, principal: SupabasePrincipal, group_id: str, period: str | None) -> RankingGroupResponseV2Model:
        self._require_active_membership(db, group_id=group_id, user_id=principal.user_id)
        period_key, period_days = self._parse_period(period)
        cache_key = f'ranking_v2:group:{group_id}:period:{period_key}'

        def _load() -> RankingGroupResponseV2Model:
            rows = self.repository.list_group_ranking(db, group_id=group_id, period_days=period_days)
            players = [
                RankingPlayerV2Model(
                    player_id=row['player_id'],
                    user_id=row.get('user_id'),
                    display_name=row['display_name'],
                    avatar_url=resolve_avatar(row.get('avatar_url')),
                    games=int(row.get('games') or 0),
                    goals=int(row.get('goals') or 0),
                    assists=int(row.get('assists') or 0),
                    own_goals=int(row.get('own_goals') or 0),
                    yellow_cards=int(row.get('yellow_cards') or 0),
                    red_cards=int(row.get('red_cards') or 0),
                    wins=int(row.get('wins') or 0),
                    draws=int(row.get('draws') or 0),
                    losses=int(row.get('losses') or 0),
                    score=self._score(row),
                    last_match_at=row.get('last_match_at'),
                )
                for row in rows
            ]
            players.sort(key=lambda item: (-item.score, -item.goals, -item.assists, item.own_goals, item.display_name.lower()))
            return RankingGroupResponseV2Model(group_id=group_id, period=period_key, generated_at=datetime.now(timezone.utc), players=players)

        return app_cache.get_or_set(cache_key, _load, ttl_seconds=20)
