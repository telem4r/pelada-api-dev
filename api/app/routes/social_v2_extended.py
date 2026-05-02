"""Social V2 Extended routes — posts, player/group ratings, player profile, network, nearby matches.

Extends the existing social_v2 routes with features from the frontend antigo that were not ported.
"""
from __future__ import annotations

from typing import Any, Optional
from math import isfinite
from datetime import datetime
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.core.supabase_storage import resolve_avatar_fields

router = APIRouter(tags=["Social V2 Extended"])


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


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


def _my_player(db: Session, user_id: str) -> dict[str, Any]:
    position_expr = _player_position_expr(db)
    rating_expr = _player_skill_expr(db)
    row = db.execute(text(f"""
        select p.id::text as player_id, u.id::text as user_id,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as name,
               coalesce(p.avatar_url, u.avatar_url) as avatar_url,
               {position_expr} as position, {rating_expr} as skill_level,
               u.current_city as city, u.birth_city, u.birth_state, u.birth_country,
               case when u.birth_date is not null then to_char(u.birth_date, 'YYYY-MM-DD') end as birth_date,
               coalesce(p.preferred_foot, u.preferred_foot) as preferred_foot,
               p.bio
        from public.players p join public.users u on u.id = p.user_id
        where p.user_id = cast(:uid as uuid) limit 1
    """), {'uid': user_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Perfil do jogador não encontrado.")
    return resolve_avatar_fields(dict(row))




def _score_bucket(value: float, buckets: list[tuple[float, int]]) -> int:
    for threshold, score in buckets:
        if value <= threshold:
            return score
    return buckets[-1][1] if buckets else 0


def _calculate_player_class(matches_played: int, ranking_points: int, social_avg: float | None, ratings_count: int) -> dict[str, Any]:
    match_points = (
        35 if matches_played >= 50 else
        31 if matches_played >= 35 else
        26 if matches_played >= 20 else
        18 if matches_played >= 10 else
        10 if matches_played >= 5 else
        5 if matches_played >= 1 else 0
    )
    perf_points = (
        30 if ranking_points >= 200 else
        27 if ranking_points >= 140 else
        22 if ranking_points >= 90 else
        16 if ranking_points >= 50 else
        10 if ranking_points >= 20 else
        5 if ranking_points > 0 else 0
    )
    avg = max(0.0, min(float(social_avg or 0.0), 5.0))
    social_points = (
        35 if avg >= 4.7 else
        30 if avg >= 4.3 else
        25 if avg >= 3.8 else
        18 if avg >= 3.2 else
        10 if avg >= 2.5 else
        5 if avg > 0 else 0
    )
    if ratings_count <= 0:
        social_points = 0
    elif ratings_count < 5:
        social_points = min(social_points, 18)

    score = int(max(0, min(100, match_points + perf_points + social_points)))

    tiers = [
        ('elite', 'Elite', 'A Lenda', 85),
        ('diamante', 'Diamante', 'A Excelência', 70),
        ('platina', 'Platina', 'O Domínio', 55),
        ('ouro', 'Ouro', 'A Consolidação', 40),
        ('prata', 'Prata', 'A Ascensão', 25),
        ('bronze', 'Bronze', 'A Base', 0),
    ]

    key, label, subtitle = 'bronze', 'Bronze', 'A Base'
    for t_key, t_label, t_subtitle, min_score in tiers:
        if score >= min_score:
            key, label, subtitle = t_key, t_label, t_subtitle
            break

    max_order = 5 if matches_played >= 50 else 4 if matches_played >= 35 else 3 if matches_played >= 20 else 2 if matches_played >= 10 else 1 if matches_played >= 5 else 0
    if ratings_count < 5:
        max_order = min(max_order, 2)
    if avg < 4.3:
        max_order = min(max_order, 4)
    if avg < 4.0:
        max_order = min(max_order, 3)

    tier_order = {'bronze': 0, 'prata': 1, 'ouro': 2, 'platina': 3, 'diamante': 4, 'elite': 5}
    reverse_tiers = {0: ('bronze','Bronze','A Base'),1: ('prata','Prata','A Ascensão'),2: ('ouro','Ouro','A Consolidação'),3: ('platina','Platina','O Domínio'),4: ('diamante','Diamante','A Excelência'),5: ('elite','Elite','A Lenda')}
    allowed = reverse_tiers[min(max_order, tier_order.get(key, 0))]
    key, label, subtitle = allowed

    return {
        'key': key,
        'label': label,
        'subtitle': subtitle,
        'score': score,
        'matches_played': matches_played,
        'ratings_count': ratings_count,
        'social_average': round(avg, 1) if ratings_count > 0 else None,
    }


def _fetch_player_class(db: Session, player_id: str) -> dict[str, Any]:
    stats_cols = _table_columns(db, 'match_player_stats_v2')
    fair_play_sum = 'coalesce(sum(s.fair_play),0)::int' if 'fair_play' in stats_cols else '0::int'
    row = db.execute(text(f"""
        select
            count(distinct s.match_id)::int as matches_played,
            coalesce(sum(s.wins),0)::int as wins,
            coalesce(sum(s.goals),0)::int as goals,
            coalesce(sum(s.assists),0)::int as assists,
            {fair_play_sum} as fair_play
        from public.match_player_stats_v2 s
        join public.matches_v2 m on m.id = s.match_id
        where s.player_id = cast(:pid as uuid) and m.status = 'finished'
    """), {'pid': player_id}).mappings().first() or {}
    matches_played = int(row.get('matches_played') or 0)
    ranking_points = matches_played * 3 + int(row.get('wins') or 0) * 5 + int(row.get('goals') or 0) + int(row.get('assists') or 0) + int(row.get('fair_play') or 0) * 2

    rep = db.execute(text("""
        select count(*)::int as count,
               avg((skill + fair_play + commitment) / 3.0)::float as score
        from public.player_ratings_v2
        where target_player_id = cast(:pid as uuid)
    """), {'pid': player_id}).mappings().first() or {}
    return _calculate_player_class(matches_played, ranking_points, rep.get('score'), int(rep.get('count') or 0))


# ═══════════════════════════════════════════════════════════════════════
# PLAYER PROFILE (detailed, with stats and groups)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/players/{player_id}/profile")
def get_player_profile(player_id: str,
                       principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                       db: Session = Depends(get_db_session)):
    _my_player(db, principal.user_id)  # require auth
    position_expr = _player_position_expr(db)
    rating_expr = _player_skill_expr(db)
    row = db.execute(text(f"""
        with stats as (
            select s.player_id,
                   count(distinct s.match_id)::int as matches_played,
                   coalesce(sum(s.goals),0)::int as goals,
                   coalesce(sum(s.assists),0)::int as assists,
                   coalesce(sum(s.wins),0)::int as wins,
                   0::int as draws,
                   0::int as losses,
                   coalesce(sum(case when s.mvp then 1 else 0 end),0)::int as mvp,
                   0.0 as win_rate,
                   0::int as unjustified_absences
            from public.match_player_stats_v2 s
            join public.matches_v2 m on m.id = s.match_id
            where s.player_id = cast(:pid as uuid)
              and m.status = 'finished'
            group by s.player_id
        )
        select p.id::text as player_id, u.id::text as user_id,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as name,
               {position_expr} as position, {rating_expr} as skill_level,
               u.current_city as city, u.birth_city, u.birth_state, u.birth_country,
               case when u.birth_date is not null then to_char(u.birth_date,'YYYY-MM-DD') end as birth_date,
               coalesce(p.preferred_foot, u.preferred_foot) as preferred_foot,
               p.bio, coalesce(p.avatar_url, u.avatar_url) as avatar_url,
               coalesce(s.matches_played, 0) as matches_played,
               coalesce(s.wins, 0) as wins, coalesce(s.draws, 0) as draws,
               coalesce(s.losses, 0) as losses, coalesce(s.goals, 0) as goals,
               coalesce(s.assists, 0) as assists, coalesce(s.mvp, 0) as mvp,
               coalesce(s.win_rate, 0) as win_rate, coalesce(s.unjustified_absences, 0) as unjustified_absences
        from public.players p join public.users u on u.id = p.user_id
        left join stats s on s.player_id = p.id
        where p.id = cast(:pid as uuid) limit 1
    """), {'pid': player_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Jogador não encontrado.")
    result = dict(row)
    # Groups
    groups = db.execute(text("""
        select g.id::text, g.name
        from public.group_members gm join public.groups g on g.id = gm.group_id
        where gm.player_id = cast(:pid as uuid) and gm.status = 'active'
    """), {'pid': player_id}).mappings().all()
    result['stats'] = {
        'matches_played': result.pop('matches_played'), 'wins': result.pop('wins'),
        'draws': result.pop('draws'), 'losses': result.pop('losses'),
        'goals': result.pop('goals'), 'assists': result.pop('assists'),
        'mvp': result.pop('mvp'), 'win_rate': result.pop('win_rate'),
        'unjustified_absences': result.pop('unjustified_absences'),
    }
    result['groups'] = [dict(g) for g in groups]
    result['player_class'] = _fetch_player_class(db, player_id)
    return resolve_avatar_fields(result)


@router.get("/v2/players/{player_id}/public-profile")
def get_player_public_profile(player_id: str,
                              principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    return get_player_profile(player_id, principal, db)


# ═══════════════════════════════════════════════════════════════════════
# PLAYER MATCHES HISTORY (global, not group-scoped)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/players/{player_id}/matches-history")
def get_player_global_history(player_id: str,
                              principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    _my_player(db, principal.user_id)
    matches_cols = _table_columns(db, 'matches_v2')
    stats_cols = _table_columns(db, 'match_player_stats_v2')
    mvp_expr = "coalesce(s.mvp, false)"
    team_number_expr = 's.team_number::int' if 'team_number' in stats_cols else 'null::int'
    title_expr = 'm.title' if 'title' in matches_cols else 'null::text'
    rows = db.execute(text(f"""
        select s.match_id::text, m.starts_at as date_time, m.group_id::text, g.name as group_name,
               'played' as result, coalesce(s.goals,0)::int as goals, coalesce(s.assists,0)::int as assists,
               {mvp_expr} as mvp, {team_number_expr} as team_number, {title_expr} as title
        from public.match_player_stats_v2 s
        join public.matches_v2 m on m.id = s.match_id
        join public.groups g on g.id = m.group_id
        where s.player_id = cast(:pid as uuid) and m.status = 'finished'
        order by m.starts_at desc limit 50
    """), {'pid': player_id}).mappings().all()
    return [resolve_avatar_fields(dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# PLAYER NETWORK
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/players/{player_id}/network")
def get_player_network(player_id: str,
                       principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                       db: Session = Depends(get_db_session)):
    _my_player(db, principal.user_id)
    rows = db.execute(text("""
        select distinct p2.id::text as player_id, u2.id::text as user_id,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u2.first_name),''), nullif(trim(u2.last_name),''))), ''), nullif(trim(p2.display_name),''), nullif(trim(p2.full_name),''), nullif(trim(u2.name),''), 'Jogador') as name,
               {position_expr} as position, p2.avatar_url, u2.current_city as city,
               0::int as shared_matches, 0::int as invited_groups_count,
               null::timestamptz as last_played_at, 0.0 as reputation_score
        from public.group_members gm1
        join public.group_members gm2 on gm2.group_id = gm1.group_id and gm2.player_id != gm1.player_id
        join public.players p2 on p2.id = gm2.player_id
        join public.users u2 on u2.id = p2.user_id
        where gm1.player_id = cast(:pid as uuid) and gm1.status = 'active' and gm2.status = 'active'
        limit 50
    """), {'pid': player_id}).mappings().all()
    return [resolve_avatar_fields(dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# PLAYER REPUTATION
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/players/{player_id}/reputation")
def get_player_reputation(player_id: str,
                          principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                          db: Session = Depends(get_db_session)):
    _my_player(db, principal.user_id)
    row = db.execute(text("""
        select count(*)::int as count,
               avg(skill)::float as skill_avg, avg(fair_play)::float as fp_avg, avg(commitment)::float as commit_avg,
               avg((skill + fair_play + commitment) / 3.0)::float as score
        from public.player_ratings_v2
        where target_player_id = cast(:pid as uuid)
    """), {'pid': player_id}).mappings().first()
    if not row or row['count'] == 0:
        return {"player_id": player_id, "score": None, "label": "Sem avaliações", "components": {}}
    score = row['score'] or 0
    label = "Excelente" if score >= 4 else "Bom" if score >= 3 else "Regular" if score >= 2 else "Baixo"
    return {
        "player_id": player_id, "score": round(score, 1), "label": label,
        "components": {"skill": round(row['skill_avg'] or 0, 1), "fair_play": round(row['fp_avg'] or 0, 1),
                       "commitment": round(row['commit_avg'] or 0, 1)},
    }


# ═══════════════════════════════════════════════════════════════════════
# PLAYER RATINGS
# ═══════════════════════════════════════════════════════════════════════

class RatePlayerPayload(BaseModel):
    match_id: Optional[str] = None
    group_id: Optional[str] = None
    skill: int = Field(3, ge=1, le=5)
    fair_play: int = Field(3, ge=1, le=5)
    commitment: int = Field(3, ge=1, le=5)


@router.post("/v2/players/{player_id}/ratings", status_code=201)
def rate_player(player_id: str, payload: RatePlayerPayload,
                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    if me['player_id'] == player_id:
        raise HTTPException(status_code=400, detail="Não pode avaliar-se a si próprio.")
    # Upsert: descarta avaliação anterior do mesmo rater para o mesmo target
    existing = db.execute(text("""
        select id from public.player_ratings_v2
        where target_player_id = cast(:tid as uuid) and rater_player_id = cast(:rid as uuid) limit 1
    """), {'tid': player_id, 'rid': me['player_id']}).scalar()
    if existing:
        db.execute(text("""
            update public.player_ratings_v2
            set skill = :skill, fair_play = :fp, commitment = :commit,
                match_id = case when :mid is not null then cast(:mid as uuid) else match_id end,
                group_id = case when :gid is not null then cast(:gid as uuid) else group_id end,
                created_at = now()
            where id = :id
        """), {'id': existing, 'skill': payload.skill, 'fp': payload.fair_play, 'commit': payload.commitment,
               'mid': payload.match_id, 'gid': payload.group_id})
    else:
        db.execute(text("""
            insert into public.player_ratings_v2 (target_player_id, rater_player_id, match_id, group_id, skill, fair_play, commitment)
            values (cast(:tid as uuid), cast(:rid as uuid),
                    case when :mid is not null then cast(:mid as uuid) end,
                    case when :gid is not null then cast(:gid as uuid) end,
                    :skill, :fp, :commit)
        """), {'tid': player_id, 'rid': me['player_id'], 'mid': payload.match_id,
               'gid': payload.group_id, 'skill': payload.skill, 'fp': payload.fair_play, 'commit': payload.commitment})
    db.flush()
    db.commit()
    return get_player_rating_summary(player_id, principal, db)


@router.get("/v2/players/{player_id}/rating-summary")
def get_player_rating_summary(player_id: str,
                              principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    _my_player(db, principal.user_id)
    row = db.execute(text("""
        select count(*)::int as count,
               avg((skill + fair_play + commitment)/3.0)::float as average,
               avg(skill)::float as skill_average,
               avg(fair_play)::float as fair_play_average,
               avg(commitment)::float as commitment_average
        from public.player_ratings_v2 where target_player_id = cast(:pid as uuid)
    """), {'pid': player_id}).mappings().first()
    return {
        "average": round(row['average'] or 0, 1) if row else 0,
        "count": row['count'] if row else 0,
        "skill_average": round(row['skill_average'] or 0, 1) if row else None,
        "fair_play_average": round(row['fair_play_average'] or 0, 1) if row else None,
        "commitment_average": round(row['commitment_average'] or 0, 1) if row else None,
    }


# ═══════════════════════════════════════════════════════════════════════
# GROUP RATINGS
# ═══════════════════════════════════════════════════════════════════════

class RateGroupPayload(BaseModel):
    organization: int = Field(3, ge=1, le=5)
    fair_play: int = Field(3, ge=1, le=5)
    level: int = Field(3, ge=1, le=5)


@router.post("/v2/groups/{group_id}/ratings", status_code=201)
def rate_group(group_id: str, payload: RateGroupPayload,
               principal: SupabasePrincipal = Depends(get_current_supabase_principal),
               db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    db.execute(text("""
        insert into public.group_ratings_v2 (group_id, rater_player_id, organization, fair_play, level)
        values (cast(:gid as uuid), cast(:pid as uuid), :org, :fp, :lev)
    """), {'gid': group_id, 'pid': me['player_id'], 'org': payload.organization,
           'fp': payload.fair_play, 'lev': payload.level})
    db.commit()
    return get_group_rating_summary(group_id, principal, db)


@router.get("/v2/groups/{group_id}/rating-summary")
def get_group_rating_summary(group_id: str,
                             principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                             db: Session = Depends(get_db_session)):
    _my_player(db, principal.user_id)
    row = db.execute(text("""
        select count(*)::int as count, avg((organization + fair_play + level)/3.0)::float as average,
               avg(organization)::float as skill_average, avg(fair_play)::float as fair_play_average,
               avg(level)::float as commitment_average
        from public.group_ratings_v2 where group_id = cast(:gid as uuid)
    """), {'gid': group_id}).mappings().first()
    return {
        "average": round(row['average'] or 0, 1) if row else 0,
        "count": row['count'] if row else 0,
        "skill_average": round(row['skill_average'] or 0, 1) if row else None,
        "fair_play_average": round(row['fair_play_average'] or 0, 1) if row else None,
        "commitment_average": round(row['commitment_average'] or 0, 1) if row else None,
    }


# ═══════════════════════════════════════════════════════════════════════
# SOCIAL POSTS
# ═══════════════════════════════════════════════════════════════════════

class PostCreate(BaseModel):
    post_type: str = "text"
    content: Optional[str] = None


@router.get("/v2/posts")
def list_posts(principal: SupabasePrincipal = Depends(get_current_supabase_principal),
               db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    position_expr = _player_position_expr(db)
    rows = db.execute(text(f"""
        select sp.id::text, sp.player_id::text, sp.post_type, sp.content, sp.snapshot, sp.created_at,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as player_name,
               coalesce(p.avatar_url, u.avatar_url) as player_avatar_url,
               (select count(*)::int from public.social_post_likes_v2 l where l.post_id = sp.id) as likes_count,
               (select count(*)::int from public.social_post_comments_v2 c where c.post_id = sp.id) as comments_count,
               exists(select 1 from public.social_post_likes_v2 l where l.post_id = sp.id and l.player_id = cast(:mpid as uuid)) as liked_by_me
        from public.social_posts_v2 sp
        join public.players p on p.id = sp.player_id
        join public.users u on u.id = p.user_id
        order by sp.created_at desc limit 50
    """), {'mpid': me['player_id']}).mappings().all()
    result = []
    for r in rows:
        comments = db.execute(text("""
            select c.id::text, c.player_id::text, c.comment, c.created_at,
                   coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as player_name,
                   coalesce(p.avatar_url, u.avatar_url) as player_avatar_url
            from public.social_post_comments_v2 c
            join public.players p on p.id = c.player_id
            join public.users u on u.id = p.user_id
            where c.post_id = cast(:pid as uuid)
            order by c.created_at asc limit 20
        """), {'pid': r['id']}).mappings().all()
        item = dict(r)
        item['comments'] = [dict(c) for c in comments]
        result.append(item)
    return resolve_avatar_fields(result)


@router.post("/v2/posts", status_code=201)
def create_post(payload: PostCreate,
                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    row = db.execute(text("""
        insert into public.social_posts_v2 (player_id, post_type, content)
        values (cast(:pid as uuid), :pt, :content)
        returning id::text, player_id::text, post_type, content, snapshot, created_at
    """), {'pid': me['player_id'], 'pt': payload.post_type, 'content': payload.content or ''}).mappings().first()
    db.commit()
    result = dict(row)
    result['player_name'] = me['name']
    result['player_avatar_url'] = me.get('avatar_url')
    result['likes_count'] = 0
    result['comments_count'] = 0
    result['liked_by_me'] = False
    result['comments'] = []
    return resolve_avatar_fields(result)


@router.post("/v2/posts/{post_id}/likes")
def toggle_like(post_id: str,
                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    existing = db.execute(text("""
        select id from public.social_post_likes_v2
        where post_id = cast(:pid as uuid) and player_id = cast(:mpid as uuid)
    """), {'pid': post_id, 'mpid': me['player_id']}).scalar()
    if existing:
        db.execute(text("delete from public.social_post_likes_v2 where id = :id"), {'id': existing})
    else:
        db.execute(text("""
            insert into public.social_post_likes_v2 (post_id, player_id)
            values (cast(:pid as uuid), cast(:mpid as uuid))
        """), {'pid': post_id, 'mpid': me['player_id']})
    db.commit()
    # Return updated post
    return {"id": post_id, "liked_by_me": not bool(existing)}


@router.post("/v2/posts/{post_id}/comments")
def add_comment_to_post(post_id: str, payload: dict,
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    comment_text = payload.get('comment', '')
    if not comment_text.strip():
        raise HTTPException(status_code=400, detail="Comentário não pode ser vazio.")
    db.execute(text("""
        insert into public.social_post_comments_v2 (post_id, player_id, comment)
        values (cast(:pid as uuid), cast(:mpid as uuid), :comment)
    """), {'pid': post_id, 'mpid': me['player_id'], 'comment': comment_text})
    db.commit()
    return {"id": post_id, "comment_added": True}


# ═══════════════════════════════════════════════════════════════════════
# FRIENDS (mapped to social_follows_v2 - bidirectional follow = friend)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/friends")
def list_friends(principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                 db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    position_expr = _player_position_expr(db)
    rows = db.execute(text(f"""
        select sf.id::text as friendship_id, p.id::text as player_id,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as name,
               {position_expr} as position, u.current_city as city, coalesce(p.avatar_url, u.avatar_url) as avatar_url,
               'accepted' as status, greatest(sf.followed_at, reverse_sf.followed_at) as requested_at
        from public.social_follows_v2 sf
        join public.social_follows_v2 reverse_sf
          on reverse_sf.follower_player_id = sf.followed_player_id
         and reverse_sf.followed_player_id = sf.follower_player_id
        join public.players p on p.id = sf.followed_player_id
        join public.users u on u.id = p.user_id
        where sf.follower_player_id = cast(:pid as uuid)
        order by greatest(sf.followed_at, reverse_sf.followed_at) desc
    """), {'pid': me['player_id']}).mappings().all()
    return [resolve_avatar_fields(dict(r)) for r in rows]


@router.get("/v2/friends/requests")
def list_friend_requests(direction: str = Query("incoming"),
                         principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                         db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    direction = (direction or 'incoming').strip().lower()
    if direction == 'outgoing':
        position_expr = _player_position_expr(db)
        rows = db.execute(text(f"""
            select n.id::text as friendship_id,
                   p.id::text as player_id,
                   coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as name,
                   {position_expr} as position,
                   u.current_city as city,
                   coalesce(p.avatar_url, u.avatar_url) as avatar_url,
                   'pending' as status,
                   n.created_at as requested_at
            from public.notification_events_v2 n
            join public.users u on u.id = n.recipient_user_id
            join public.players p on p.user_id = u.id
            where n.actor_user_id = cast(:uid as uuid)
              and n.event_type = 'friend_request'
              and coalesce(n.is_read, false) = false
            order by n.created_at desc
        """), {'uid': principal.user_id}).mappings().all()
    else:
        position_expr = _player_position_expr(db)
        rows = db.execute(text(f"""
            select n.id::text as friendship_id,
                   p.id::text as player_id,
                   coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as name,
                   {position_expr} as position,
                   u.current_city as city,
                   coalesce(p.avatar_url, u.avatar_url) as avatar_url,
                   'pending' as status,
                   n.created_at as requested_at
            from public.notification_events_v2 n
            join public.users u on u.id = n.actor_user_id
            join public.players p on p.user_id = u.id
            where n.recipient_user_id = cast(:uid as uuid)
              and n.event_type = 'friend_request'
              and coalesce(n.is_read, false) = false
            order by n.created_at desc
        """), {'uid': principal.user_id}).mappings().all()
    return [resolve_avatar_fields(dict(r)) for r in rows]


@router.get("/v2/friends/search")
def search_friends(q: str = Query(""),
                   principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                   db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    if not q.strip():
        return []
    position_expr = _player_position_expr(db)
    rows = db.execute(text(f"""
        select '0' as friendship_id, p.id::text as player_id,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as name,
               {position_expr} as position, u.current_city as city, coalesce(p.avatar_url, u.avatar_url) as avatar_url,
               case
                 when exists(
                    select 1 from public.social_follows_v2 sf
                    join public.social_follows_v2 rsf
                      on rsf.follower_player_id = sf.followed_player_id
                     and rsf.followed_player_id = sf.follower_player_id
                    where sf.follower_player_id = cast(:pid as uuid) and sf.followed_player_id = p.id
                 ) then 'accepted'
                 when exists(
                    select 1 from public.notification_events_v2 n
                    where n.actor_user_id = cast(:uid as uuid)
                      and n.recipient_user_id = u.id
                      and n.event_type = 'friend_request'
                      and coalesce(n.is_read, false) = false
                 ) then 'pending'
                 else 'none'
               end as status,
               null as requested_at
        from public.players p join public.users u on u.id = p.user_id
        where p.id != cast(:pid as uuid)
          and lower(coalesce(u.email, '')) like :q
        order by lower(coalesce(u.email, '')) asc
        limit 20
    """), {'pid': me['player_id'], 'uid': principal.user_id, 'q': f"%{q.strip().lower()}%"}).mappings().all()
    return [resolve_avatar_fields(dict(r)) for r in rows]


@router.post("/v2/friends/requests", status_code=201)
def send_friend_request(payload: dict,
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    target_pid = payload.get('player_id')
    if not target_pid:
        raise HTTPException(status_code=400, detail="player_id é obrigatório.")
    if me['player_id'] == target_pid:
        raise HTTPException(status_code=400, detail="Não pode seguir-se a si próprio.")
    position_expr = _player_position_expr(db)
    target = db.execute(text(f"""
        select p.id::text as player_id, u.id::text as user_id,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as name,
               {position_expr} as position, u.current_city as city, coalesce(p.avatar_url, u.avatar_url) as avatar_url
        from public.players p join public.users u on u.id = p.user_id
        where p.id = cast(:pid as uuid) limit 1
    """), {'pid': target_pid}).mappings().first()
    if not target:
        raise HTTPException(status_code=404, detail="Jogador não encontrado.")

    already_friends = db.execute(text("""
        select exists(
            select 1
            from public.social_follows_v2 sf
            join public.social_follows_v2 rsf
              on rsf.follower_player_id = sf.followed_player_id
             and rsf.followed_player_id = sf.follower_player_id
            where sf.follower_player_id = cast(:me as uuid)
              and sf.followed_player_id = cast(:target as uuid)
        )
    """), {'me': me['player_id'], 'target': target_pid}).scalar()
    if already_friends:
        raise HTTPException(status_code=400, detail="Vocês já são amigos.")

    pending_id = db.execute(text("""
        select id::text
        from public.notification_events_v2
        where actor_user_id = cast(:actor_uid as uuid)
          and recipient_user_id = cast(:recipient_uid as uuid)
          and event_type = 'friend_request'
          and coalesce(is_read, false) = false
        limit 1
    """), {'actor_uid': principal.user_id, 'recipient_uid': target['user_id']}).scalar()
    if pending_id:
        return {
            "friendship_id": pending_id,
            "player_id": target_pid,
            "name": target['name'],
            "position": target.get('position'),
            "city": target.get('city'),
            "avatar_url": target.get('avatar_url'),
            "status": "pending",
            "requested_at": datetime.utcnow().isoformat(),
        }

    payload_data = {
        "action_type": "friend_request",
        "player_id": me['player_id'],
        "player_name": me['name'],
        "requester_user_id": principal.user_id,
    }
    notification_id = db.execute(text("""
        INSERT INTO public.notification_events_v2
            (id, recipient_user_id, actor_user_id, event_type, title, message, payload, created_at, is_read)
        VALUES (gen_random_uuid(), cast(:uid as uuid), cast(:actor_uid as uuid), 'friend_request',
                'Nova solicitação de amizade', :msg, cast(:payload as jsonb), now(), false)
        RETURNING id::text
    """), {
        'uid': target['user_id'],
        'actor_uid': principal.user_id,
        'msg': f'{me["name"]} enviou uma solicitação de amizade.',
        'payload': json.dumps(payload_data),
    }).scalar_one()
    db.commit()
    return {
        "friendship_id": notification_id,
        "player_id": target_pid,
        "name": target['name'],
        "position": target.get('position'),
        "city": target.get('city'),
        "avatar_url": target.get('avatar_url'),
        "status": "pending",
        "requested_at": datetime.utcnow().isoformat(),
    }


@router.post("/v2/friends/requests/{friendship_id}/accept")
def accept_friend(friendship_id: str,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    me = _my_player(db, principal.user_id)
    row = db.execute(text("""
        select id::text as friendship_id,
               actor_user_id::text as requester_user_id,
               payload
        from public.notification_events_v2
        where id = cast(:fid as uuid)
          and recipient_user_id = cast(:uid as uuid)
          and event_type = 'friend_request'
        limit 1
    """), {'fid': friendship_id, 'uid': principal.user_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail='Solicitação não encontrada.')
    requester = _my_player(db, row['requester_user_id'])
    db.execute(text("""
        insert into public.social_follows_v2 (follower_player_id, followed_player_id)
        values (cast(:me as uuid), cast(:target as uuid))
        on conflict do nothing
    """), {'me': me['player_id'], 'target': requester['player_id']})
    db.execute(text("""
        insert into public.social_follows_v2 (follower_player_id, followed_player_id)
        values (cast(:me as uuid), cast(:target as uuid))
        on conflict do nothing
    """), {'me': requester['player_id'], 'target': me['player_id']})
    db.execute(text("""
        update public.notification_events_v2
        set is_read = true, read_at = now()
        where id = cast(:fid as uuid)
    """), {'fid': friendship_id})
    db.commit()
    return {
        "friendship_id": friendship_id,
        "player_id": requester['player_id'],
        "name": requester['name'],
        "position": requester.get('position'),
        "city": requester.get('city'),
        "avatar_url": requester.get('avatar_url'),
        "status": "accepted",
        "requested_at": datetime.utcnow().isoformat(),
    }


@router.post("/v2/friends/requests/{friendship_id}/reject")
def reject_friend(friendship_id: str,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    result = db.execute(text("""
        update public.notification_events_v2
        set is_read = true, read_at = now()
        where id = cast(:fid as uuid)
          and recipient_user_id = cast(:uid as uuid)
          and event_type = 'friend_request'
    """), {'fid': friendship_id, 'uid': principal.user_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail='Solicitação não encontrada.')
    db.commit()
    return {"friendship_id": friendship_id, "status": "rejected"}


# ═══════════════════════════════════════════════════════════════════════
# REVIEW PROMPTS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/players/me/review-prompts")
def get_review_prompts(principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                       db: Session = Depends(get_db_session)):
    return []


# ═══════════════════════════════════════════════════════════════════════
# NEARBY MATCHES
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/matches/nearby")
def get_nearby_matches(lat: float = Query(...), lng: float = Query(...), radius_km: float = Query(10, gt=0, le=50),
                       limit: int = Query(50, ge=1, le=100),
                       principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                       db: Session = Depends(get_db_session)):
    _my_player(db, principal.user_id)
    rows = db.execute(text("""
        with public_matches as (
            select
                m.id::text as match_id,
                m.group_id::text as group_id,
                g.name as group_name,
                coalesce(m.modality, g.modality, 'Futebol') as game_type,
                coalesce(nullif(trim(m.title), ''), g.name, 'Partida pública') as title,
                m.starts_at,
                coalesce(nullif(trim(m.location_name), ''), 'Local não informado') as location_name,
                coalesce(nullif(trim(m.location_name), ''), 'Local não informado') as venue_name,
                m.city,
                m.location_lat,
                m.location_lng,
                greatest(coalesce(m.line_slots, 0) + coalesce(m.goalkeeper_slots, 0), 0)::int as player_limit,
                coalesce((
                    select count(*)
                    from public.match_participants_v2 mp
                    where mp.match_id = m.id and mp.status = 'confirmado'
                ), 0)::int
                +
                coalesce((
                    select count(*)
                    from public.match_guests_v2 mg
                    where mg.match_id = m.id and mg.status = 'confirmado'
                ), 0)::int as confirmed_count,
                (6371.0 * acos(
                    least(1.0, greatest(-1.0,
                        cos(radians(:lat)) * cos(radians(m.location_lat)) *
                        cos(radians(m.location_lng) - radians(:lng)) +
                        sin(radians(:lat)) * sin(radians(m.location_lat))
                    ))
                )) as distance_km
            from public.matches_v2 m
            join public.groups g on g.id = m.group_id
            where coalesce(m.is_public, false) = true
              and m.location_lat is not null
              and m.location_lng is not null
              and m.starts_at is not null
              and m.starts_at >= now()
              and coalesce(m.status, 'scheduled') in ('scheduled', 'in_progress')
        )
        select
            match_id,
            group_id,
            group_name,
            game_type,
            title,
            starts_at,
            venue_name,
            location_name,
            city,
            location_lat,
            location_lng,
            player_limit,
            confirmed_count,
            greatest(player_limit - confirmed_count, 0)::int as available_spots,
            round(distance_km::numeric, 1) as distance_km
        from public_matches
        where distance_km <= :radius_km
        order by distance_km asc, starts_at asc
        limit :limit
    """), {'lat': lat, 'lng': lng, 'radius_km': radius_km, 'limit': limit}).mappings().all()

    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item['match_type'] = item.get('game_type') or 'Futebol'
        result.append(item)
    return result


# ═══════════════════════════════════════════════════════════════════════
# FEED (extended - uses notification_events_v2 as feed source)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/feed")
def get_feed(principal: SupabasePrincipal = Depends(get_current_supabase_principal),
             db: Session = Depends(get_db_session)):
    rows = db.execute(text("""
        select n.id::text, n.event_type, n.title, n.message as subtitle,
               n.created_at, n.group_id::text, n.payload,
               null as actor_player_id, null as actor_name, null as actor_avatar_url,
               null as target_player_id, null as target_name, null as match_id
        from public.notification_events_v2 n
        where n.recipient_user_id = cast(:uid as uuid)
        order by n.created_at desc limit 50
    """), {'uid': principal.user_id}).mappings().all()
    result = []
    for r in rows:
        item = dict(r)
        item['metadata'] = item.pop('payload', {})
        # Try extract group_name from metadata
        meta = item.get('metadata') or {}
        item['group_name'] = meta.get('group_name')
        result.append(item)
    return resolve_avatar_fields(result)


# ═══════════════════════════════════════════════════════════════════════
# GROUP INVITE PLAYER
# ═══════════════════════════════════════════════════════════════════════

@router.post("/v2/groups/{group_id}/invite-player", status_code=201)
def invite_player_to_group(group_id: str, payload: dict,
                           principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                           db: Session = Depends(get_db_session)):
    # Find player's email and create invitation
    player_id = payload.get('player_id')
    if not player_id:
        raise HTTPException(status_code=400, detail="player_id é obrigatório.")
    row = db.execute(text("""
        select u.email from public.players p join public.users u on u.id = p.user_id
        where p.id = cast(:pid as uuid) limit 1
    """), {'pid': player_id}).scalar()
    if not row:
        raise HTTPException(status_code=404, detail="Jogador não encontrado.")
    # Create invitation
    db.execute(text("""
        insert into public.group_invitations (id, group_id, invited_email, invited_by_user_id, status, token, created_at)
        values (gen_random_uuid(), cast(:gid as uuid), :email, cast(:uid as uuid), 'pending', gen_random_uuid()::text, now())
        on conflict do nothing
    """), {'gid': group_id, 'email': row, 'uid': principal.user_id})
    db.commit()
    return {"status": "invited"}
