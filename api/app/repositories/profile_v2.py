from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


class ProfileV2Repository:
    def _table_columns(self, db: Session, table_name: str) -> set[str]:
        try:
            return {col["name"] for col in inspect(db.bind).get_columns(table_name)}
        except Exception:
            return set()

    def fetch_me(self, db: Session, *, user_id: str) -> dict[str, Any] | None:
        user_cols = self._table_columns(db, 'users')
        player_cols = self._table_columns(db, 'players')

        def u(col: str, alias: str | None = None) -> str:
            out = alias or col
            return f"u.{col} as {out}" if col in user_cols else f"null as {out}"

        def p(col: str, alias: str | None = None) -> str:
            out = alias or col
            return f"p.{col} as {out}" if col in player_cols else f"null as {out}"

        user_name_expr = "u.name" if 'name' in user_cols else "null"
        user_first_name_expr = "u.first_name" if 'first_name' in user_cols else "null"
        user_last_name_expr = "u.last_name" if 'last_name' in user_cols else "null"
        user_full_name_expr = (
            f"concat_ws(' ', nullif(trim({user_first_name_expr}), ''), nullif(trim({user_last_name_expr}), ''))"
            if 'first_name' in user_cols or 'last_name' in user_cols
            else "null"
        )
        player_display_expr = "p.display_name" if 'display_name' in player_cols else "null"
        player_full_expr = "p.full_name" if 'full_name' in player_cols else "null"
        player_avatar_expr = "p.avatar_url" if 'avatar_url' in player_cols else "null"
        user_avatar_expr = "u.avatar_url" if 'avatar_url' in user_cols else "null"
        player_position_expr = "p.position" if 'position' in player_cols else "null"
        user_position_expr = "u.position" if 'position' in user_cols else "null"
        player_foot_expr = "p.preferred_foot" if 'preferred_foot' in player_cols else "null"
        user_foot_expr = "u.preferred_foot" if 'preferred_foot' in user_cols else "null"
        player_id_expr = "p.id::text as player_id_str" if 'id' in player_cols else "null as player_id_str"

        sql = f"""
            select
                u.id::text as id,
                {player_id_expr},
                coalesce(
                    nullif(trim({user_full_name_expr}), ''),
                    nullif(trim({user_name_expr}), ''),
                    nullif(trim({player_display_expr}), ''),
                    nullif(trim({player_full_expr}), ''),
                    'Jogador'
                ) as name,
                {u('email')},
                coalesce(nullif(trim({player_avatar_expr}), ''), nullif(trim({user_avatar_expr}), '')) as avatar_url,
                {u('first_name')},
                {u('last_name')},
                {u('birth_date')},
                {u('favorite_team')},
                {u('birth_country')},
                {u('birth_state')},
                {u('birth_city')},
                {u('current_country')},
                {u('current_state')},
                {u('current_city')},
                coalesce(nullif(trim({player_position_expr}), ''), nullif(trim({user_position_expr}), '')) as position,
                coalesce(nullif(trim({player_foot_expr}), ''), nullif(trim({user_foot_expr}), '')) as preferred_foot,
                {u('language')}
            from public.users u
            left join public.players p on p.user_id = u.id
            where u.id = cast(:user_id as uuid)
            limit 1
        """
        row = db.execute(text(sql), {'user_id': user_id}).mappings().first()
        return dict(row) if row else None

    def update_me(self, db: Session, *, user_id: str, data: dict[str, Any]) -> None:
        user_cols = self._table_columns(db, 'users')
        player_cols = self._table_columns(db, 'players')

        current = db.execute(text("""
            select first_name, last_name, name
            from public.users
            where id = cast(:user_id as uuid)
            limit 1
        """), {'user_id': user_id}).mappings().first() or {}

        user_fields = [
            'first_name', 'last_name', 'birth_date', 'favorite_team',
            'birth_country', 'birth_state', 'birth_city',
            'current_country', 'current_state', 'current_city',
            'position', 'preferred_foot', 'language',
        ]
        user_data = {k: v for k, v in data.items() if k in user_fields and k in user_cols}

        first_name = data.get('first_name', current.get('first_name'))
        last_name = data.get('last_name', current.get('last_name'))
        full_name = ' '.join([part.strip() for part in [first_name or '', last_name or ''] if part and part.strip()]).strip()
        if full_name and 'name' in user_cols:
            user_data['name'] = full_name

        if user_data:
            clauses = ', '.join(f"{k} = :{k}" for k in user_data.keys())
            if 'updated_at' in user_cols:
                clauses = f"{clauses}, updated_at = now()"
            db.execute(
                text(f"update public.users set {clauses} where id = cast(:user_id as uuid)"),
                {'user_id': user_id, **user_data},
            )

        player_updates: dict[str, Any] = {}
        if 'position' in data:
            if 'position' in player_cols:
                player_updates['position'] = data.get('position')
            if 'primary_position' in player_cols:
                player_updates['primary_position'] = data.get('position')
        if 'preferred_foot' in data and 'preferred_foot' in player_cols:
            player_updates['preferred_foot'] = data.get('preferred_foot')
        if 'current_city' in data and 'city' in player_cols:
            player_updates['city'] = data.get('current_city')
        if 'avatar_url' in data and 'avatar_url' in player_cols:
            player_updates['avatar_url'] = data.get('avatar_url')
        if full_name:
            if 'display_name' in player_cols:
                player_updates['display_name'] = full_name
            if 'full_name' in player_cols:
                player_updates['full_name'] = full_name
        if player_updates:
            clauses = ', '.join(f"{k} = :{k}" for k in player_updates.keys())
            if 'updated_at' in player_cols:
                clauses = f"{clauses}, updated_at = now()"
            db.execute(
                text(f"update public.players set {clauses} where user_id = cast(:user_id as uuid)"),
                {'user_id': user_id, **player_updates},
            )

    def get_reputation(self, db: Session, *, player_id: str) -> dict[str, Any] | None:
        player_exists = db.execute(text("""
            select p.id::text as player_id
            from public.players p
            where p.id = cast(:player_id as uuid)
            limit 1
        """), {'player_id': player_id}).mappings().first()
        if not player_exists:
            return None

        row = db.execute(text("""
            select
                cast(:player_id as uuid)::text as player_id,
                count(*)::int as ratings_count,
                avg(skill)::float as skill_avg,
                avg(fair_play)::float as fair_play_avg,
                avg(commitment)::float as commitment_avg,
                avg((skill + fair_play + commitment) / 3.0)::float as score
            from public.player_ratings_v2
            where target_player_id = cast(:player_id as uuid)
        """), {'player_id': player_id}).mappings().first()

        if not row:
            return {
                'player_id': player_id,
                'score': None,
                'label': 'Sem reputação',
                'components': {},
            }

        item = dict(row)
        ratings_count = int(item.get('ratings_count') or 0)
        score = item.get('score')
        if ratings_count == 0 or score is None:
            return {
                'player_id': item['player_id'],
                'score': None,
                'label': 'Sem reputação',
                'components': {},
            }

        score_value = round(float(score), 1)
        if score_value >= 4.5:
            label = 'Excelente'
        elif score_value >= 3.5:
            label = 'Bom'
        elif score_value >= 2.5:
            label = 'Regular'
        else:
            label = 'Baixo'

        return {
            'player_id': item['player_id'],
            'score': score_value,
            'label': label,
            'components': {
                'skill': round(float(item.get('skill_avg') or 0), 1),
                'fair_play': round(float(item.get('fair_play_avg') or 0), 1),
                'commitment': round(float(item.get('commitment_avg') or 0), 1),
                'count': ratings_count,
            },
        }
