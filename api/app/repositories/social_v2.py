from __future__ import annotations
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session


def _table_columns(db: Session, table_name: str) -> set[str]:
    rows = db.execute(text("""
        select column_name
        from information_schema.columns
        where table_schema = 'public' and table_name = :table_name
    """), {'table_name': table_name}).scalars().all()
    return {str(row) for row in rows}


def _table_column_types(db: Session, table_name: str) -> dict[str, str]:
    rows = db.execute(text("""
        select column_name, data_type
        from information_schema.columns
        where table_schema = 'public' and table_name = :table_name
    """), {'table_name': table_name}).mappings().all()
    return {str(row['column_name']): str(row['data_type']) for row in rows}


def _player_position_expr(db: Session, *, player_alias: str = 'p', user_alias: str | None = 'u') -> str:
    player_cols = _table_columns(db, 'players')
    user_cols = _table_columns(db, 'users') if user_alias else set()
    exprs: list[str] = []
    if 'primary_position' in player_cols:
        exprs.append(f"{player_alias}.primary_position")
    if 'position' in player_cols:
        exprs.append(f"{player_alias}.position")
    if user_alias and 'position' in user_cols:
        exprs.append(f"{user_alias}.position")
    if not exprs:
        return 'null::text'
    return f"coalesce({', '.join(exprs)})"


def _nullable_int_expr(db: Session, *, table_name: str, column_name: str, table_alias: str) -> str:
    column_types = _table_column_types(db, table_name)
    data_type = column_types.get(column_name)
    if not data_type:
        return 'null::int'
    expr = f"{table_alias}.{column_name}"
    numeric_types = {'smallint', 'integer', 'bigint'}
    if data_type in numeric_types:
        return f"{expr}::int"
    if data_type in {'numeric', 'real', 'double precision'}:
        return f"round({expr})::int"
    return (
        f"case when nullif(trim({expr}::text), '') ~ '^[0-9]+$' " 
        f"then ({expr}::text)::int else null end"
    )


def _player_skill_expr(db: Session, *, player_alias: str = 'p') -> str:
    skill_level_expr = _nullable_int_expr(db, table_name='players', column_name='skill_level', table_alias=player_alias)
    rating_expr = _nullable_int_expr(db, table_name='players', column_name='rating', table_alias=player_alias)
    return f"coalesce({skill_level_expr}, {rating_expr}, 0)"

class SocialV2Repository:
    def fetch_my_player(self, db: Session, *, user_id: str) -> dict[str, Any] | None:
        position_expr = _player_position_expr(db)
        skill_expr = _player_skill_expr(db)
        row = db.execute(text(f"""
            select p.id::text as player_id,
                   u.id::text as user_id,
                   coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as display_name,
                   {position_expr} as position,
                   coalesce(nullif(trim(p.city), ''), nullif(trim(u.current_city), '')) as city,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url,
                   p.bio,
                   {skill_expr} as skill_level,
                   u.birth_city,
                   u.birth_state,
                   u.birth_country,
                   case when u.birth_date is not null then to_char(u.birth_date, 'YYYY-MM-DD') else null end as birth_date,
                   coalesce(nullif(trim(p.preferred_foot), ''), nullif(trim(u.preferred_foot), '')) as preferred_foot
            from public.players p
            join public.users u on u.id = p.user_id
            where p.user_id = cast(:user_id as uuid)
            limit 1
        """), {'user_id': user_id}).mappings().first()
        return dict(row) if row else None

    def fetch_player_profile(self, db: Session, *, player_id: str) -> dict[str, Any] | None:
        position_expr = _player_position_expr(db)
        skill_expr = _player_skill_expr(db)
        row = db.execute(text(f"""
            with stats as (
                select s.player_id,
                       count(*)::int as matches_played,
                       coalesce(sum(s.goals), 0)::int as goals,
                       coalesce(sum(s.assists), 0)::int as assists
                from public.match_player_stats_v2 s
                where s.player_id = cast(:player_id as uuid)
                group by s.player_id
            ), rank_score as (
                select player_id,
                       (coalesce(sum(goals),0) * 5 + coalesce(sum(assists),0) * 3 - coalesce(sum(own_goals),0) * 4 - coalesce(sum(yellow_cards),0) - coalesce(sum(red_cards),0) * 3 + count(*)::int) as ranking_score
                from public.match_player_stats_v2
                where player_id = cast(:player_id as uuid)
                group by player_id
            )
            select p.id::text as player_id,
                   u.id::text as user_id,
                   coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as display_name,
                   {position_expr} as position,
                   coalesce(nullif(trim(p.city), ''), nullif(trim(u.current_city), '')) as city,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url,
                   p.bio,
                   {skill_expr} as skill_level,
                   u.birth_city,
                   u.birth_state,
                   u.birth_country,
                   case when u.birth_date is not null then to_char(u.birth_date, 'YYYY-MM-DD') else null end as birth_date,
                   coalesce(nullif(trim(p.preferred_foot), ''), nullif(trim(u.preferred_foot), '')) as preferred_foot,
                   coalesce(r.ranking_score, 0)::int as ranking_score,
                   coalesce(s.matches_played, 0)::int as matches_played,
                   coalesce(s.goals, 0)::int as goals,
                   coalesce(s.assists, 0)::int as assists
            from public.players p
            left join public.users u on u.id = p.user_id
            left join stats s on s.player_id = p.id
            left join rank_score r on r.player_id = p.id
            where p.id = cast(:player_id as uuid)
            limit 1
        """), {'player_id': player_id}).mappings().first()
        return dict(row) if row else None

    def search_players(self, db: Session, *, query: str, current_player_id: str, limit: int = 20) -> list[dict[str, Any]]:
        position_expr = _player_position_expr(db)
        skill_expr = _player_skill_expr(db)
        rows = db.execute(text(f"""
            with stats as (
                select s.player_id,
                       count(*)::int as matches_played,
                       coalesce(sum(s.goals), 0)::int as goals,
                       coalesce(sum(s.assists), 0)::int as assists
                from public.match_player_stats_v2 s
                group by s.player_id
            )
            select p.id::text as player_id,
                   u.id::text as user_id,
                   coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as display_name,
                   {position_expr} as position,
                   coalesce(nullif(trim(p.city), ''), nullif(trim(u.current_city), '')) as city,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url,
                   p.bio,
                   {skill_expr} as skill_level,
                   u.birth_city,
                   u.birth_state,
                   u.birth_country,
                   case when u.birth_date is not null then to_char(u.birth_date, 'YYYY-MM-DD') else null end as birth_date,
                   coalesce(nullif(trim(p.preferred_foot), ''), nullif(trim(u.preferred_foot), '')) as preferred_foot,
                   0::int as ranking_score,
                   coalesce(s.matches_played, 0)::int as matches_played,
                   coalesce(s.goals, 0)::int as goals,
                   coalesce(s.assists, 0)::int as assists
            from public.players p
            left join public.users u on u.id = p.user_id
            left join stats s on s.player_id = p.id
            where p.id <> cast(:current_player_id as uuid)
              and (
                    coalesce(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), '')), p.display_name, '') ilike :term
                 or coalesce(p.full_name, '') ilike :term
                 or coalesce(u.name, '') ilike :term
                 or coalesce(p.city, '') ilike :term
              )
            order by coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') asc
            limit :limit
        """), {'current_player_id': current_player_id, 'term': f'%{query.strip()}%', 'limit': limit}).mappings().all()
        return [dict(r) for r in rows]

    def create_follow(self, db: Session, *, follower_player_id: str, followed_player_id: str) -> dict[str, Any]:
        row = db.execute(text("""
            insert into public.social_follows_v2 (follower_player_id, followed_player_id)
            values (cast(:follower_player_id as uuid), cast(:followed_player_id as uuid))
            on conflict (follower_player_id, followed_player_id)
            do update set followed_at = now()
            returning id::text as id, followed_at
        """), {'follower_player_id': follower_player_id, 'followed_player_id': followed_player_id}).mappings().first()
        return dict(row)

    def delete_follow(self, db: Session, *, follower_player_id: str, followed_player_id: str) -> None:
        db.execute(text("""
            delete from public.social_follows_v2
            where follower_player_id = cast(:follower_player_id as uuid)
              and followed_player_id = cast(:followed_player_id as uuid)
        """), {'follower_player_id': follower_player_id, 'followed_player_id': followed_player_id})

    def list_following(self, db: Session, *, follower_player_id: str) -> list[dict[str, Any]]:
        position_expr = _player_position_expr(db)
        rows = db.execute(text(f"""
            select sf.id::text as id,
                   sf.followed_player_id::text as target_player_id,
                   coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as target_display_name,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url,
                   {position_expr} as position,
                   coalesce(nullif(trim(p.city), ''), nullif(trim(u.current_city), '')) as city,
                   sf.followed_at
            from public.social_follows_v2 sf
            join public.players p on p.id = sf.followed_player_id
            left join public.users u on u.id = p.user_id
            where sf.follower_player_id = cast(:follower_player_id as uuid)
            order by sf.followed_at desc
        """), {'follower_player_id': follower_player_id}).mappings().all()
        return [dict(r) for r in rows]

    def list_feed(self, db: Session, *, follower_player_id: str, limit: int = 40) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            with following as (
                select followed_player_id from public.social_follows_v2 where follower_player_id = cast(:follower_player_id as uuid)
            ), ranking as (
                select player_id,
                       (coalesce(sum(goals),0) * 5 + coalesce(sum(assists),0) * 3 - coalesce(sum(own_goals),0) * 4 - coalesce(sum(yellow_cards),0) - coalesce(sum(red_cards),0) * 3 + count(*)::int) as ranking_score
                from public.match_player_stats_v2
                group by player_id
            ), match_events as (
                select
                    ('match-' || m.id::text || '-' || s.player_id::text)::text as id,
                    'partida_finalizada'::text as event_type,
                    coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as actor_display_name,
                    p.id::text as actor_player_id,
                    p.avatar_url as actor_avatar_url,
                    ('Partida finalizada: ' || coalesce(m.title, 'Partida'))::text as title,
                    ('Desempenho: ' || coalesce(s.goals,0)::text || ' gol(s) e ' || coalesce(s.assists,0)::text || ' assistência(s).')::text as description,
                    m.finished_at as occurred_at,
                    m.group_id::text as group_id,
                    g.name as group_name,
                    m.id::text as match_id
                from public.match_player_stats_v2 s
                join public.matches_v2 m on m.id = s.match_id
                join public.players p on p.id = s.player_id
                left join public.users u on u.id = p.user_id
                left join public.groups g on g.id = m.group_id
                where s.player_id in (select followed_player_id from following)
                  and m.finished_at is not null
            ), rank_events as (
                select
                    ('rank-' || p.id::text)::text as id,
                    'ranking_atualizado'::text as event_type,
                    coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as actor_display_name,
                    p.id::text as actor_player_id,
                    p.avatar_url as actor_avatar_url,
                    'Ranking atualizado'::text as title,
                    ('Pontuação atual: ' || coalesce(r.ranking_score,0)::text) as description,
                    now() as occurred_at,
                    null::text as group_id,
                    null::text as group_name,
                    null::text as match_id
                from public.players p
                left join public.users u on u.id = p.user_id
                left join ranking r on r.player_id = p.id
                where p.id in (select followed_player_id from following)
            )
            select * from (
                select * from match_events
                union all
                select * from rank_events
            ) feed
            order by occurred_at desc
            limit :limit
        """), {'follower_player_id': follower_player_id, 'limit': limit}).mappings().all()
        return [dict(r) for r in rows]
