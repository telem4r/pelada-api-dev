"""Stats V2 routes — leaderboard, history, achievements, highlights, player/group stats, match summary, MVP.

Uses V2 UUID-native tables (matches_v2, match_events_v2, match_player_stats_v2, match_draws_v2, etc).
"""
from __future__ import annotations

from typing import Any, Optional
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.services.avatar_resolver import resolve_avatars

router = APIRouter(tags=["Stats V2"])


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _resolve_identity(db: Session, user_id: str) -> dict[str, Any]:
    row = db.execute(text("""
        select u.id::text as user_id, p.id::text as player_id,
               coalesce(nullif(trim(u.name), ''), nullif(trim(p.display_name), ''), 'Jogador') as name
        from public.users u join public.players p on p.user_id = u.id
        where u.id = cast(:uid as uuid) limit 1
    """), {'uid': user_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado.")
    return dict(row)


def _column_exists(db: Session, table_name: str, column_name: str, *, schema: str = "public") -> bool:
    return bool(db.execute(text("""
        select exists (
            select 1
            from information_schema.columns
            where table_schema = :schema
              and table_name = :table_name
              and column_name = :column_name
        )
    """), {"schema": schema, "table_name": table_name, "column_name": column_name}).scalar())


def _match_summary_query(db: Session) -> str:
    has_mvp_player = _column_exists(db, 'matches_v2', 'mvp_player_id')
    has_mvp_guest = _column_exists(db, 'matches_v2', 'mvp_guest_id')
    mvp_player_sql = 'm.mvp_player_id::text as mvp_player_id' if has_mvp_player else 'null::text as mvp_player_id'
    mvp_guest_sql = 'm.mvp_guest_id::text as mvp_guest_id' if has_mvp_guest else 'null::text as mvp_guest_id'
    return f"""
        select m.id::text as match_id,
               m.status::text,
               (m.finished_at is not null) as finished,
               {mvp_player_sql},
               {mvp_guest_sql}
        from public.matches_v2 m
        where m.id = cast(:mid as uuid)
          and m.group_id = cast(:gid as uuid)
        limit 1
    """


def _require_group_member(db: Session, group_id: str, user_id: str) -> dict[str, Any]:
    row = db.execute(text("""
        select gm.role::text, gm.status::text, gm.player_id::text
        from public.group_members gm
        join public.players p on p.id = gm.player_id
        where gm.group_id = cast(:gid as uuid) and p.user_id = cast(:uid as uuid) limit 1
    """), {'gid': group_id, 'uid': user_id}).mappings().first()
    if not row or row['status'] != 'active':
        raise HTTPException(status_code=403, detail="Não é membro ativo deste grupo.")
    return dict(row)


def _auto_repair_match_status(db: Session, group_id: str) -> None:
    """Marca como finished partidas com stats manuais gravados mas ainda inconsistentes."""
    try:
        db.execute(text("""
            UPDATE public.matches_v2
            SET status = 'finished',
                finished_at = COALESCE(finished_at, now()),
                updated_at = now()
            WHERE group_id = cast(:gid as uuid)
              AND status != 'finished'
              AND EXISTS (
                  SELECT 1
                  FROM public.match_player_stats_v2 s
                  WHERE s.match_id = matches_v2.id
                    AND s.entry_kind = 'member'
                    AND s.team_number = 0
              )
        """), {'gid': group_id})
        db.commit()
    except SQLAlchemyError:
        db.rollback()


# ═══════════════════════════════════════════════════════════════════════
# MATCH SUMMARY (score + MVP)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/matches/{match_id}/summary")
def get_match_summary(group_id: str, match_id: str,
                      principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                      db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    match = db.execute(
        text(_match_summary_query(db)),
        {'mid': match_id, 'gid': group_id},
    ).mappings().first()
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada.")
    # Score from events. A ausência de draw/eventos persistidos não pode derrubar a rota.
    team1 = 0
    team2 = 0
    try:
        scores = db.execute(text("""
            select e.team_number,
                   count(*) filter (where e.event_type = 'goal') as goals
            from public.match_events_v2 e
            where e.match_id = cast(:mid as uuid)
              and e.team_number > 0
            group by e.team_number order by e.team_number
        """), {'mid': match_id}).mappings().all()
        for s in scores:
            if s['team_number'] == 1:
                team1 = s['goals']
            elif s['team_number'] == 2:
                team2 = s['goals']
    except SQLAlchemyError:
        team1 = 0
        team2 = 0
    # MVP name
    mvp_name = None
    try:
        if match['mvp_player_id']:
            r = db.execute(text("select display_name from public.players where id = cast(:pid as uuid)"),
                           {'pid': match['mvp_player_id']}).scalar()
            mvp_name = r
        elif match['mvp_guest_id']:
            r = db.execute(text("select name from public.match_guests_v2 where id = cast(:gid as uuid)"),
                           {'gid': match['mvp_guest_id']}).scalar()
            mvp_name = r
    except SQLAlchemyError:
        mvp_name = None
    winner = None
    if team1 > team2:
        winner = 1
    elif team2 > team1:
        winner = 2
    return {
        "match_id": match['match_id'],
        "status": match['status'],
        "finished": match['finished'],
        "team1": team1,
        "team2": team2,
        "winner_team": winner,
        "mvp_name": mvp_name,
        "mvp": {"name": mvp_name} if mvp_name else None,
    }


@router.post("/v2/groups/{group_id}/matches/{match_id}/mvp")
def set_match_mvp(group_id: str, match_id: str,
                  payload: dict = Body(...),
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    has_mvp_player = _column_exists(db, 'matches_v2', 'mvp_player_id')
    has_mvp_guest = _column_exists(db, 'matches_v2', 'mvp_guest_id')
    if not has_mvp_player or not has_mvp_guest:
        raise HTTPException(status_code=409, detail='O MVP ainda não está disponível nesta versão da partida.')

    player_id = payload.get('player_id')
    guest_id = payload.get('guest_id')
    if player_id:
        db.execute(text("""
            update public.matches_v2 set mvp_player_id = cast(:pid as uuid), mvp_guest_id = null
            where id = cast(:mid as uuid) and group_id = cast(:gid as uuid)
        """), {'pid': player_id, 'mid': match_id, 'gid': group_id})
    elif guest_id:
        db.execute(text("""
            update public.matches_v2 set mvp_guest_id = cast(:gid2 as uuid), mvp_player_id = null
            where id = cast(:mid as uuid) and group_id = cast(:gid as uuid)
        """), {'gid2': guest_id, 'mid': match_id, 'gid': group_id})
    db.commit()
    return get_match_summary(group_id, match_id, principal, db)


# ═══════════════════════════════════════════════════════════════════════
# MATCHES HISTORY
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# LEADERBOARD / STATS RANKINGS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/stats/leaderboard")
def get_leaderboard(group_id: str,
                    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                    db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    _auto_repair_match_status(db, group_id)
    rows = db.execute(text("""
        with total_finished as (
            select count(*)::int as total
            from public.matches_v2 m
            where m.group_id = cast(:gid as uuid)
              and m.status = 'finished'
        )
        select
            s.player_id::text,
            p.user_id::text as user_id,
            coalesce(
                nullif(trim(concat_ws(' ',
                    nullif(trim(u.first_name), ''),
                    nullif(trim(u.last_name), '')
                )), ''),
                nullif(trim(p.display_name), ''),
                nullif(trim(p.full_name), ''),
                nullif(trim(u.name), ''),
                'Jogador'
            ) as name,
            coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url,
            count(distinct s.match_id)::int as games_played,
            coalesce(sum(s.goals), 0)::int as goals,
            coalesce(sum(s.assists), 0)::int as assists,
            coalesce(sum(s.wins), 0)::int as wins,
            0::int as losses,
            0::int as draws,
            coalesce(sum(case when s.mvp then 1 else 0 end), 0)::int as mvp,
            coalesce(sum(s.fair_play), 0)::int as fair_play,
            0.0 as win_rate,
            0.0 as goals_per_game,
            coalesce(p.rating, 0)::int as skill_rating,
            (
                count(distinct s.match_id) * 3 +
                coalesce(sum(s.wins), 0) * 5 +
                coalesce(sum(s.fair_play), 0) * 2 +
                coalesce(sum(s.goals), 0) +
                coalesce(sum(s.assists), 0)
            )::int as ranking_points,
            0::int as ranking_position,
            case when (select total from total_finished) > 0
                then round((count(distinct s.match_id)::numeric * 100.0) / (select total from total_finished), 2)
                else 0 end as attendance_rate,
            case
                when (count(distinct s.match_id) * 3 + coalesce(sum(s.wins), 0) * 5 + coalesce(sum(s.fair_play), 0) * 2) >= 40 then 'elite'
                when (count(distinct s.match_id) * 3 + coalesce(sum(s.wins), 0) * 5 + coalesce(sum(s.fair_play), 0) * 2) >= 20 then 'em alta'
                else 'em evolução'
            end as performance_tier,
            coalesce(sum(s.fair_play), 0)::float as reputation_score,
            case
                when coalesce(sum(s.fair_play), 0) >= 12 then 'Excelente'
                when coalesce(sum(s.fair_play), 0) >= 8 then 'Confiável'
                when coalesce(sum(s.fair_play), 0) >= 4 then 'Regular'
                else 'Sem histórico'
            end as reputation_label,
            0::int as unjustified_absences,
            0::int as abandonments
        from public.match_player_stats_v2 s
        join public.matches_v2 m on m.id = s.match_id
        join public.players p on p.id = s.player_id
        join public.users u on u.id = p.user_id
        where m.group_id = cast(:gid as uuid)
          and m.status = 'finished'
          and s.player_id is not null
        group by s.player_id, p.user_id, u.first_name, u.last_name, p.display_name, p.full_name, u.name, p.avatar_url, u.avatar_url, p.rating
        order by ranking_points desc, wins desc, attendance_rate desc, goals desc, assists desc
    """), {'gid': group_id}).mappings().all()
    players = [dict(r) for r in rows]
    for i, player in enumerate(players):
        player['ranking_position'] = i + 1
        games = player['games_played'] or 1
        player['goals_per_game'] = round(player['goals'] / games, 2)
        player['win_rate'] = round((player['wins'] / games) * 100, 2) if games else 0
    return {"players": resolve_avatars(players)}

@router.get("/v2/groups/{group_id}/stats/rankings")
def get_rankings_by_category(group_id: str,
                             principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                             db: Session = Depends(get_db_session)):
    lb = get_leaderboard(group_id, principal, db)
    players = lb.get('players', [])
    categories = [
        {"key": "goals", "title": "Artilheiros", "players": sorted(players, key=lambda x: -x['goals'])[:10]},
        {"key": "assists", "title": "Assistências", "players": sorted(players, key=lambda x: -x['assists'])[:10]},
        {"key": "games", "title": "Mais Jogos", "players": sorted(players, key=lambda x: -x['games_played'])[:10]},
        {"key": "ranking", "title": "Ranking Geral", "players": sorted(players, key=lambda x: -x['ranking_points'])[:10]},
    ]
    return {"categories": categories}


# ═══════════════════════════════════════════════════════════════════════
# PLAYER STATS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/players/{player_id}/stats")
def get_player_stats(group_id: str, player_id: str,
                     principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                     db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    row = db.execute(text("""
        select
            s.player_id::text,
            coalesce(nullif(trim(p.display_name), ''), 'Jogador') as name,
            count(distinct s.match_id)::int as games,
            coalesce(sum(s.goals), 0)::int as goals,
            coalesce(sum(s.assists), 0)::int as assists,
            coalesce(sum(s.own_goals), 0)::int as own_goals,
            coalesce(sum(s.yellow_cards), 0)::int as yellow_cards,
            coalesce(sum(s.red_cards), 0)::int as red_cards,
            coalesce(sum(s.wins), 0)::int as wins,
            0::int as losses,
            0::int as draws,
            coalesce(sum(case when s.mvp then 1 else 0 end), 0)::int as mvp,
            coalesce(sum(s.fair_play), 0)::int as fair_play,
            0.0 as win_rate,
            0.0 as goals_per_game,
            coalesce(p.rating, 0)::int as skill_rating,
            0::int as ranking_points,
            0::int as ranking_position,
            0.0 as attendance_rate,
            'em evolução' as performance_tier,
            coalesce(sum(s.fair_play), 0)::float as reputation_score,
            case
                when coalesce(sum(s.fair_play), 0) >= 12 then 'Excelente'
                when coalesce(sum(s.fair_play), 0) >= 8 then 'Confiável'
                when coalesce(sum(s.fair_play), 0) >= 4 then 'Regular'
                else 'Sem histórico'
            end as reputation_label,
            0::int as unjustified_absences,
            0::int as abandonments
        from public.match_player_stats_v2 s
        join public.matches_v2 m on m.id = s.match_id
        join public.players p on p.id = s.player_id
        where m.group_id = cast(:gid as uuid) and s.player_id = cast(:pid as uuid) and m.status = 'finished'
        group by s.player_id, p.display_name, p.rating
    """), {'gid': group_id, 'pid': player_id}).mappings().first()
    if not row:
        return {"player_id": player_id, "name": "Jogador", "games": 0, "wins": 0, "losses": 0,
                "draws": 0, "goals": 0, "assists": 0, "mvp": 0, "win_rate": 0, "goals_per_game": 0,
                "skill_rating": 0, "ranking_points": 0, "ranking_position": 0, "attendance_rate": 0,
                "performance_tier": "em evolução", "reputation_score": 0, "reputation_label": "Sem histórico",
                "unjustified_absences": 0, "abandonments": 0, "skill_evolution": [], "achievements": []}
    result = dict(row)
    games = result['games'] or 0
    computed_games = games or 1
    result['goals_per_game'] = round(result['goals'] / computed_games, 2)
    result['win_rate'] = round((result['wins'] / computed_games) * 100, 2) if games else 0
    result['ranking_points'] = (
        result['games'] * 3 +
        result['wins'] * 5 +
        result.get('fair_play', 0) * 2 +
        result['goals'] +
        result['assists']
    )
    result['skill_evolution'] = []
    result['achievements'] = []
    return result


# ═══════════════════════════════════════════════════════════════════════
# PLAYER MATCHES HISTORY
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/players/{player_id}/matches-history")
def get_player_matches_history(group_id: str, player_id: str,
                               principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                               db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    _auto_repair_match_status(db, group_id)
    rows = db.execute(text("""
        select s.match_id::text, m.starts_at as date, m.title,
               'played' as result,
               coalesce(s.goals, 0)::int as goals,
               coalesce(s.assists, 0)::int as assists,
               (exists(
                    select 1
                    from public.match_player_stats_v2 mps2
                    where mps2.match_id = m.id
                      and mps2.player_id = cast(:pid as uuid)
                      and mps2.mvp = true
                )) as mvp,
               s.team_number::int,
               0::int as team1, 0::int as team2
        from public.match_player_stats_v2 s
        join public.matches_v2 m on m.id = s.match_id
        where m.group_id = cast(:gid as uuid) and s.player_id = cast(:pid as uuid) and m.status = 'finished'
        order by m.starts_at desc limit 50
    """), {'gid': group_id, 'pid': player_id}).mappings().all()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# MATCH HIGHLIGHTS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/matches/{match_id}/highlights")
def get_match_highlights(group_id: str, match_id: str,
                         principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                         db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    summary = get_match_summary(group_id, match_id, principal, db)
    # Top scorer
    scorer = db.execute(text("""
        select coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), g.name, 'Jogador') as name, coalesce(s.goals, 0)::int as value
        from public.match_player_stats_v2 s
        left join public.players p on p.id = s.player_id
        left join public.users u on u.id = p.user_id
        left join public.match_guests_v2 g on g.id = s.guest_id
        where s.match_id = cast(:mid as uuid)
        order by s.goals desc nulls last limit 1
    """), {'mid': match_id}).mappings().first()
    assistant_row = db.execute(text("""
        select coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), g.name, 'Jogador') as name, coalesce(s.assists, 0)::int as value
        from public.match_player_stats_v2 s
        left join public.players p on p.id = s.player_id
        left join public.users u on u.id = p.user_id
        left join public.match_guests_v2 g on g.id = s.guest_id
        where s.match_id = cast(:mid as uuid)
        order by s.assists desc nulls last limit 1
    """), {'mid': match_id}).mappings().first()
    return {
        "match_id": match_id,
        "score": {"team1": summary['team1'], "team2": summary['team2']},
        "mvp": {"name": summary.get('mvp_name')} if summary.get('mvp_name') else None,
        "top_scorer": dict(scorer) if scorer and scorer['value'] > 0 else None,
        "top_assistant": dict(assistant_row) if assistant_row and assistant_row['value'] > 0 else None,
    }


# ═══════════════════════════════════════════════════════════════════════
# PLAYER ACHIEVEMENTS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/players/{player_id}/achievements")
def get_player_achievements(group_id: str, player_id: str,
                            principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                            db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    # Calculate achievements from stats
    stats = get_player_stats(group_id, player_id, principal, db)
    achievements = []
    defs = [
        ("first_match", "Estreia", "Jogar a primeira partida", "⚽", "games", 1),
        ("10_matches", "Veterano", "Jogar 10 partidas", "🏆", "games", 10),
        ("first_goal", "Primeiro Gol", "Marcar o primeiro gol", "🥅", "goals", 1),
        ("10_goals", "Artilheiro", "Marcar 10 gols", "🎯", "goals", 10),
        ("first_assist", "Garçom", "Dar a primeira assistência", "🤝", "assists", 1),
    ]
    for code, title, desc, emoji, metric, target in defs:
        current = stats.get(metric, 0)
        achievements.append({
            "code": code, "title": title, "description": desc, "emoji": emoji,
            "metric": metric, "target": target, "current": current,
            "unlocked": current >= target, "unlocked_at": None,
        })
    return {"achievements": achievements}


# ═══════════════════════════════════════════════════════════════════════
# GROUP OVERVIEW STATS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/stats/group")
def get_group_stats(group_id: str,
                    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                    db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    _auto_repair_match_status(db, group_id)
    row = db.execute(text("""
        select
            count(*)::int as total_matches,
            count(*) filter (where status = 'finished')::int as finished_matches,
            coalesce(avg(goals_count), 0) as average_goals_per_match
        from (
            select m.id, m.status,
                   (select count(*) from public.match_events_v2 e where e.match_id = m.id and e.event_type = 'goal') as goals_count
            from public.matches_v2 m where m.group_id = cast(:gid as uuid)
        ) sub
    """), {'gid': group_id}).mappings().first()
    members_count = db.execute(text("""
        select count(*)::int from public.group_members where group_id = cast(:gid as uuid) and status = 'active'
    """), {'gid': group_id}).scalar() or 0
    return {
        "total_matches": row['total_matches'] if row else 0,
        "finished_matches": row['finished_matches'] if row else 0,
        "average_goals_per_match": round(float(row['average_goals_per_match']), 1) if row else 0,
        "players_count": members_count,
        "average_skill": 0,
        "most_present": None, "most_wins": None, "top_scorer": None, "top_mvp": None, "top_reputation": None,
        "skill_distribution": {},
    }