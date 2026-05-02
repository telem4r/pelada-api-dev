from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.db import get_db
from app.models import (
    GroupMember,
    Match,
    MatchDrawTeam,
    MatchEvent,
    MatchGuestPlayer,
    MatchParticipant,
    MatchStatus,
    Player,
    PlayerAchievement,
)
from app.permissions import get_group_member
from app.security import get_current_user

router = APIRouter(tags=["Groups - Stats"])


ACHIEVEMENT_LIBRARY = [
    {
        "code": "first_win",
        "title": "Primeira vitória",
        "description": "Conquistou a primeira vitória no grupo.",
        "emoji": "🏅",
        "metric": "wins",
        "target": 1,
    },
    {
        "code": "first_goal",
        "title": "Primeiro gol",
        "description": "Marcou o primeiro gol no grupo.",
        "emoji": "⚽",
        "metric": "goals",
        "target": 1,
    },
    {
        "code": "goals_10",
        "title": "Artilheiro em ascensão",
        "description": "Alcançou 10 gols no grupo.",
        "emoji": "🔥",
        "metric": "goals",
        "target": 10,
    },
    {
        "code": "games_50",
        "title": "Veterano do grupo",
        "description": "Disputou 50 partidas no grupo.",
        "emoji": "🛡️",
        "metric": "games",
        "target": 50,
    },
    {
        "code": "mvp_5",
        "title": "Craque do grupo",
        "description": "Recebeu 5 prêmios de MVP.",
        "emoji": "👑",
        "metric": "mvp",
        "target": 5,
    },
    {
        "code": "wins_10",
        "title": "Colecionador de vitórias",
        "description": "Chegou a 10 vitórias no grupo.",
        "emoji": "🏆",
        "metric": "wins",
        "target": 10,
    },
]


def _normalize_match_status(status: str | None) -> str:
    raw = (status or "").lower()
    if raw == "canceled":
        return "cancelled"
    return raw


def _safe_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _player_name_map(db: Session, group_id: str) -> dict[int, str]:
    members = db.query(GroupMember).filter(GroupMember.group_id == group_id).all()
    player_ids = [gm.player_id for gm in members if getattr(gm, "player_id", None)]
    if not player_ids:
        return {}
    players = db.query(Player).filter(Player.id.in_(player_ids)).all()
    return {p.id: p.name for p in players}


def _player_owner_map(db: Session, group_id: str) -> dict[int, int | None]:
    members = db.query(GroupMember).filter(GroupMember.group_id == group_id).all()
    player_ids = [gm.player_id for gm in members if getattr(gm, "player_id", None)]
    if not player_ids:
        return {}
    players = db.query(Player).filter(Player.id.in_(player_ids)).all()
    return {p.id: getattr(p, "owner_id", None) for p in players}


def _player_skill_map(db: Session, group_id: str) -> dict[int, int]:
    members = db.query(GroupMember).filter(GroupMember.group_id == group_id).all()
    return {gm.player_id: int(getattr(gm, "skill_rating", 0) or 0) for gm in members if getattr(gm, "player_id", None)}


def _guest_name_map(db: Session, match_id: int) -> dict[int, str]:
    guests = db.query(MatchGuestPlayer).filter(MatchGuestPlayer.match_id == match_id).all()
    return {g.id: g.name for g in guests}


def _draw_maps(draw_teams: list[MatchDrawTeam]) -> tuple[dict[int, int], dict[int, int]]:
    team_by_player: dict[int, int] = {}
    team_by_guest: dict[int, int] = {}
    for dt in draw_teams:
        for item in (dt.players or []):
            if not isinstance(item, dict):
                continue
            if item.get("kind") == "player" and item.get("player_id"):
                team_by_player[int(item["player_id"])] = int(dt.team_number)
            if item.get("kind") == "guest" and item.get("guest_id"):
                team_by_guest[int(item["guest_id"])] = int(dt.team_number)
    return team_by_player, team_by_guest


def _match_score_payload(db: Session, match: Match) -> dict[str, Any]:
    goals = (
        db.query(MatchEvent)
        .filter(MatchEvent.match_id == match.id, MatchEvent.event_type == "goal")
        .order_by(MatchEvent.id.asc())
        .all()
    )

    team1 = 0
    team2 = 0
    name_by_player = _player_name_map(db, match.group_id)
    name_by_guest = _guest_name_map(db, match.id)

    goals_out: list[dict[str, Any]] = []
    for g in goals:
        team_number = int(getattr(g, "team_number", 0) or 0)
        if team_number == 1:
            team1 += 1
        elif team_number == 2:
            team2 += 1

        player_name = name_by_player.get(g.player_id) if getattr(g, "player_id", None) else None
        guest_name = name_by_guest.get(g.guest_id) if getattr(g, "guest_id", None) else None

        goals_out.append(
            {
                "id": g.id,
                "team": team_number,
                "team_number": team_number,
                "minute": getattr(g, "minute", None),
                "player_id": getattr(g, "player_id", None),
                "guest_id": getattr(g, "guest_id", None),
                "player_name": player_name,
                "guest_name": guest_name,
                "name": player_name or guest_name or "Jogador",
            }
        )

    return {
        "match_id": match.id,
        "team1": team1,
        "team2": team2,
        "goals": goals_out,
    }


def _match_summary_payload(db: Session, match: Match) -> dict[str, Any]:
    score = _match_score_payload(db, match)

    winner_team = None
    if score["team1"] > score["team2"]:
        winner_team = 1
    elif score["team2"] > score["team1"]:
        winner_team = 2

    mvp = None
    if getattr(match, "mvp_player_id", None):
        name_by_player = _player_name_map(db, match.group_id)
        mvp = {
            "type": "player",
            "player_id": match.mvp_player_id,
            "name": name_by_player.get(match.mvp_player_id, "Jogador"),
        }
    elif getattr(match, "mvp_guest_id", None):
        name_by_guest = _guest_name_map(db, match.id)
        mvp = {
            "type": "guest",
            "guest_id": match.mvp_guest_id,
            "name": name_by_guest.get(match.mvp_guest_id, "Convidado"),
        }

    return {
        "match_id": match.id,
        "status": match.status,
        "starts_at": _safe_iso(match.starts_at),
        "ends_at": _safe_iso(getattr(match, "ends_at", None)),
        "title": match.title,
        "score": score,
        "winner_team": winner_team,
        "mvp": mvp,
    }


def _finished_matches(db: Session, group_id: str) -> list[Match]:
    finished_value = MatchStatus.finished.value if hasattr(MatchStatus, "finished") else "finished"
    return (
        db.query(Match)
        .filter(Match.group_id == group_id, Match.status == finished_value)
        .order_by(Match.starts_at.desc(), Match.id.desc())
        .all()
    )


def _build_player_stats_map(db: Session, group_id: str) -> dict[int, dict[str, Any]]:
    members = db.query(GroupMember).filter(GroupMember.group_id == group_id).all()
    player_ids = [gm.player_id for gm in members if getattr(gm, "player_id", None)]
    name_by_player = _player_name_map(db, group_id)
    user_by_player = _player_owner_map(db, group_id)
    skill_by_player = _player_skill_map(db, group_id)

    stats: dict[int, dict[str, Any]] = {
        pid: {
            "player_id": pid,
            "user_id": user_by_player.get(pid),
            "name": name_by_player.get(pid, "Jogador"),
            "games": 0,
            "games_played": 0,
            "goals": 0,
            "assists": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "mvp": 0,
            "win_balance": 0,
            "win_rate": 0.0,
            "goals_per_game": 0.0,
            "skill_rating": skill_by_player.get(pid, 0),
            "unjustified_absences": 0,
            "abandonments": 0,
            "reputation_score": 0.0,
            "reputation_label": "Sem histórico",
            "skill_evolution": [{"label": "Atual", "skill_rating": skill_by_player.get(pid, 0)}],
        }
        for pid in player_ids
    }

    matches = _finished_matches(db, group_id)
    for m in matches:
        goals = db.query(MatchEvent).filter(MatchEvent.match_id == m.id, MatchEvent.event_type == "goal").all()
        assists = db.query(MatchEvent).filter(MatchEvent.match_id == m.id, MatchEvent.event_type == "assist").all()
        team1 = sum(1 for e in goals if int(getattr(e, "team_number", 0) or 0) == 1)
        team2 = sum(1 for e in goals if int(getattr(e, "team_number", 0) or 0) == 2)
        winner = 1 if team1 > team2 else 2 if team2 > team1 else None

        participants = db.query(MatchParticipant).filter(MatchParticipant.match_id == m.id).all()
        has_arrived = any(bool(getattr(p, "arrived", False)) for p in participants)
        draw_teams = db.query(MatchDrawTeam).filter(MatchDrawTeam.match_id == m.id).all()
        team_by_player, _ = _draw_maps(draw_teams)

        for p in participants:
            if has_arrived and not getattr(p, "arrived", False):
                continue
            pid = getattr(p, "player_id", None)
            if pid in stats and getattr(p, "no_show", False):
                stats[pid]["unjustified_absences"] += 1
                continue
            if pid in stats and bool(getattr(p, "left_early", False)):
                stats[pid]["abandonments"] += 1
            if pid not in stats:
                continue
            stats[pid]["games"] += 1
            stats[pid]["games_played"] += 1
            t = team_by_player.get(pid)
            if winner is None:
                stats[pid]["draws"] += 1
            elif t == winner:
                stats[pid]["wins"] += 1
            elif t is not None:
                stats[pid]["losses"] += 1

        for e in goals:
            pid = getattr(e, "player_id", None)
            if pid in stats:
                stats[pid]["goals"] += 1
        for e in assists:
            pid = getattr(e, "player_id", None)
            if pid in stats:
                stats[pid]["assists"] += 1

        if getattr(m, "mvp_player_id", None) in stats:
            stats[m.mvp_player_id]["mvp"] += 1

    total_finished_matches = len(matches)
    for item in stats.values():
        games = int(item["games"] or 0)
        wins = int(item["wins"] or 0)
        losses = int(item["losses"] or 0)
        goals = int(item["goals"] or 0)
        item["win_balance"] = wins - losses
        item["win_rate"] = round((wins / games) * 100, 1) if games else 0.0
        item["goals_per_game"] = round((goals / games), 2) if games else 0.0
        item["attendance_rate"] = round((games / total_finished_matches) * 100, 1) if total_finished_matches else 0.0
        item["ranking_points"] = _ranking_points(item)
        item["performance_tier"] = _performance_tier(item)
        item["reputation_score"] = _reputation_score(item)
        item["reputation_label"] = _reputation_label(float(item["reputation_score"] or 0.0), games > 0 or int(item.get("unjustified_absences", 0) or 0) > 0 or int(item.get("abandonments", 0) or 0) > 0)

    ordered = sorted(stats.values(), key=_leaderboard_sort_key)
    for idx, item in enumerate(ordered, start=1):
        item["ranking_position"] = idx

    return stats


def _ranking_points(item: dict[str, Any]) -> int:
    return (
        int(item.get("goals", 0)) * 5
        + int(item.get("mvp", 0)) * 4
        + int(item.get("wins", 0)) * 3
        + int(item.get("assists", 0)) * 2
        + int(item.get("games", 0))
        + int(item.get("win_balance", 0))
    )


def _performance_tier(item: dict[str, Any]) -> str:
    points = _ranking_points(item)
    games = int(item.get("games", 0) or 0)
    wins = int(item.get("wins", 0) or 0)
    goals = int(item.get("goals", 0) or 0)
    if games >= 12 and wins >= 7 and points >= 45:
        return "elite"
    if games >= 6 and (wins >= 3 or goals >= 4 or points >= 20):
        return "destaque"
    if games >= 3:
        return "ativo"
    return "em evolução"




def _reputation_score(item: dict[str, Any]) -> float:
    games = int(item.get("games", 0) or 0)
    no_show = int(item.get("unjustified_absences", 0) or 0)
    abandonments = int(item.get("abandonments", 0) or 0)
    if games <= 0 and no_show <= 0 and abandonments <= 0:
        return 0.0

    base = 3.0
    participation_bonus = min(games * 0.12, 1.0)
    attendance_bonus = min((float(item.get("attendance_rate", 0.0) or 0.0) / 100.0) * 1.0, 1.0)
    no_show_penalty = min(no_show * 0.8, 2.4)
    abandonment_penalty = min(abandonments * 1.0, 2.0)

    score = base + participation_bonus + attendance_bonus - no_show_penalty - abandonment_penalty
    return round(max(0.0, min(5.0, score)), 1)


def _reputation_label(score: float, has_history: bool) -> str:
    if not has_history:
        return "Sem histórico"
    if score >= 4.5:
        return "Excelente"
    if score >= 3.5:
        return "Confiável"
    if score >= 2.5:
        return "Regular"
    return "Atenção"


def _leaderboard_sort_key(item: dict[str, Any]):
    score = _ranking_points(item)
    return (-score, -int(item.get("goals", 0)), -int(item.get("mvp", 0)), -int(item.get("wins", 0)), item.get("name", ""))


def _achievement_payloads(db: Session, group_id: str, player_stats: dict[str, Any]) -> list[dict[str, Any]]:
    metric_values = {
        "wins": int(player_stats.get("wins", 0) or 0),
        "goals": int(player_stats.get("goals", 0) or 0),
        "games": int(player_stats.get("games", 0) or 0),
        "mvp": int(player_stats.get("mvp", 0) or 0),
    }
    player_id = int(player_stats.get("player_id") or 0)
    persisted = {
        row.code: row
        for row in db.query(PlayerAchievement)
        .filter(PlayerAchievement.group_id == group_id, PlayerAchievement.player_id == player_id)
        .all()
    }
    results: list[dict[str, Any]] = []
    for spec in ACHIEVEMENT_LIBRARY:
        current = metric_values.get(spec["metric"], 0)
        unlocked = current >= spec["target"] or spec["code"] in persisted
        persisted_row = persisted.get(spec["code"])
        results.append(
            {
                "code": spec["code"],
                "title": spec["title"],
                "description": spec["description"],
                "emoji": spec["emoji"],
                "metric": spec["metric"],
                "target": spec["target"],
                "current": current,
                "unlocked": unlocked,
                "unlocked_at": _safe_iso(getattr(persisted_row, "unlocked_at", None)) if persisted_row else (_safe_iso(utc_now()) if unlocked else None),
            }
        )
    results.sort(key=lambda x: (not x["unlocked"], -int(x["current"]), x["title"]))
    return results


@router.get("/groups/{group_id}/matches/{match_id}/summary")
def match_summary(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)

    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return _match_summary_payload(db, match)


@router.post("/groups/{group_id}/matches/{match_id}/mvp")
def set_match_mvp(
    group_id: str,
    match_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    member = get_group_member(db, group_id, current_user_id)
    role = (getattr(member, "role", "") or "").lower()
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only owner/admin can set MVP")

    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    player_id = payload.get("player_id")
    guest_id = payload.get("guest_id")
    if not player_id and not guest_id:
        raise HTTPException(status_code=400, detail="player_id or guest_id is required")

    match.mvp_player_id = player_id
    match.mvp_guest_id = guest_id
    db.commit()
    db.refresh(match)

    return _match_summary_payload(db, match)


@router.get("/groups/{group_id}/matches/history")
def group_matches_history(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)

    cancelled_value = "cancelled"
    canceled_value = MatchStatus.canceled.value if hasattr(MatchStatus, "canceled") else "canceled"
    finished_value = MatchStatus.finished.value if hasattr(MatchStatus, "finished") else "finished"

    matches = (
        db.query(Match)
        .filter(Match.group_id == group_id)
        .filter(Match.status.in_([finished_value, cancelled_value, canceled_value]))
        .order_by(Match.starts_at.desc(), Match.id.desc())
        .all()
    )

    out = []
    for m in matches:
        score = _match_score_payload(db, m)
        summ = _match_summary_payload(db, m)
        out.append(
            {
                "match_id": m.id,
                "starts_at": _safe_iso(m.starts_at),
                "ends_at": _safe_iso(getattr(m, "ends_at", None)),
                "title": m.title,
                "status": _normalize_match_status(m.status),
                "team1": score["team1"],
                "team2": score["team2"],
                "winner_team": summ["winner_team"],
                "mvp_name": summ["mvp"]["name"] if summ.get("mvp") else None,
            }
        )
    return out


@router.get("/groups/{group_id}/stats/leaderboard")
def group_stats_leaderboard(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    players_out = list(_build_player_stats_map(db, group_id).values())
    players_out.sort(key=_leaderboard_sort_key)
    return {"players": players_out}


@router.get("/groups/{group_id}/ranking")
def group_ranking(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    players_out = list(_build_player_stats_map(db, group_id).values())
    players_out.sort(key=_leaderboard_sort_key)
    return {"players": players_out}


@router.get("/groups/{group_id}/players/{player_id}/stats")
def player_group_stats(
    group_id: str,
    player_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    stats = _build_player_stats_map(db, group_id)
    if player_id not in stats:
        raise HTTPException(status_code=404, detail="Player not found in group")
    player = stats[player_id]
    payload = dict(player)
    payload["achievements"] = _achievement_payloads(db, group_id, player)
    return payload


@router.get("/groups/{group_id}/stats/rankings")
def rankings_by_category(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    players = list(_build_player_stats_map(db, group_id).values())

    def top_by(field: str, title: str):
        ordered = sorted(players, key=lambda x: (-int(x.get(field, 0) or 0), x.get("name", "")))
        return {
            "key": field,
            "title": title,
            "players": ordered[:5],
        }

    return {
        "categories": [
            top_by("goals", "Artilheiros"),
            top_by("assists", "Assistências"),
            top_by("mvp", "MVP"),
            top_by("wins", "Vitórias"),
            top_by("games", "Participações"),
            top_by("reputation_score", "Reputação"),
        ]
    }


@router.get("/groups/{group_id}/players/{player_id}/matches-history")
def player_matches_history(
    group_id: str,
    player_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    stats_map = _build_player_stats_map(db, group_id)
    if player_id not in stats_map:
        raise HTTPException(status_code=404, detail="Player not found in group")

    out: list[dict[str, Any]] = []
    matches = _finished_matches(db, group_id)
    for m in matches:
        participants = db.query(MatchParticipant).filter(MatchParticipant.match_id == m.id, MatchParticipant.player_id == player_id).all()
        if not participants:
            continue
        participant = participants[0]
        if getattr(participant, "no_show", False):
            continue

        goals = db.query(MatchEvent).filter(MatchEvent.match_id == m.id, MatchEvent.event_type == "goal").all()
        assists = db.query(MatchEvent).filter(MatchEvent.match_id == m.id, MatchEvent.event_type == "assist").all()
        team1 = sum(1 for e in goals if int(getattr(e, "team_number", 0) or 0) == 1)
        team2 = sum(1 for e in goals if int(getattr(e, "team_number", 0) or 0) == 2)
        winner = 1 if team1 > team2 else 2 if team2 > team1 else None
        draw_teams = db.query(MatchDrawTeam).filter(MatchDrawTeam.match_id == m.id).all()
        team_by_player, _ = _draw_maps(draw_teams)
        player_team = team_by_player.get(player_id)

        result = "draw"
        if winner is not None and player_team is not None:
            result = "win" if winner == player_team else "loss"

        out.append(
            {
                "match_id": m.id,
                "date": _safe_iso(m.starts_at),
                "title": m.title,
                "result": result,
                "goals": sum(1 for e in goals if getattr(e, "player_id", None) == player_id),
                "assists": sum(1 for e in assists if getattr(e, "player_id", None) == player_id),
                "mvp": getattr(m, "mvp_player_id", None) == player_id,
                "team_number": player_team,
                "team1": team1,
                "team2": team2,
            }
        )
    return out


@router.get("/groups/{group_id}/matches/{match_id}/highlights")
def match_highlights(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    goals = db.query(MatchEvent).filter(MatchEvent.match_id == match_id, MatchEvent.event_type == "goal").all()
    assists = db.query(MatchEvent).filter(MatchEvent.match_id == match_id, MatchEvent.event_type == "assist").all()
    name_by_player = _player_name_map(db, group_id)
    name_by_guest = _guest_name_map(db, match_id)

    goal_counter = Counter()
    assist_counter = Counter()
    for e in goals:
        if getattr(e, "player_id", None):
            goal_counter[("player", e.player_id)] += 1
        elif getattr(e, "guest_id", None):
            goal_counter[("guest", e.guest_id)] += 1
    for e in assists:
        if getattr(e, "player_id", None):
            assist_counter[("player", e.player_id)] += 1
        elif getattr(e, "guest_id", None):
            assist_counter[("guest", e.guest_id)] += 1

    def pack_leader(counter: Counter, fallback_title: str):
        if not counter:
            return None
        (kind, entity_id), value = counter.most_common(1)[0]
        if kind == "player":
            return {"type": kind, "player_id": entity_id, "name": name_by_player.get(entity_id, "Jogador"), "value": value}
        return {"type": kind, "guest_id": entity_id, "name": name_by_guest.get(entity_id, "Convidado"), "value": value}

    summary = _match_summary_payload(db, match)
    return {
        "match_id": match_id,
        "score": summary["score"],
        "mvp": summary.get("mvp"),
        "top_scorer": pack_leader(goal_counter, "Artilheiro"),
        "top_assistant": pack_leader(assist_counter, "Assistente"),
    }


@router.get("/groups/{group_id}/players/{player_id}/achievements")
def player_achievements(
    group_id: str,
    player_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    stats = _build_player_stats_map(db, group_id)
    if player_id not in stats:
        raise HTTPException(status_code=404, detail="Player not found in group")
    return {"achievements": _achievement_payloads(db, group_id, stats[player_id])}


@router.get("/groups/{group_id}/stats/group")
def group_stats_overview(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    players = list(_build_player_stats_map(db, group_id).values())
    history = group_matches_history(group_id, db, current_user_id)
    finished = [m for m in history if (m.get("status") or "") == "finished"]
    total_goals = sum(int(m.get("team1", 0) or 0) + int(m.get("team2", 0) or 0) for m in finished)
    avg_goals = round((total_goals / len(finished)), 2) if finished else 0.0
    skill_values = [int(p.get("skill_rating", 0) or 0) for p in players if int(p.get("skill_rating", 0) or 0) > 0]
    skill_distribution = dict(sorted(Counter(skill_values).items())) if skill_values else {}

    def best_player(field: str):
        if not players:
            return None
        ordered = sorted(players, key=lambda x: (-int(x.get(field, 0) or 0), x.get("name", "")))
        top = ordered[0]
        if int(top.get(field, 0) or 0) <= 0:
            return None
        return {
            "player_id": top["player_id"],
            "name": top["name"],
            "value": int(top.get(field, 0) or 0),
            "metric": field,
        }

    return {
        "total_matches": len(history),
        "finished_matches": len(finished),
        "average_goals_per_match": avg_goals,
        "most_present": best_player("games"),
        "most_wins": best_player("wins"),
        "top_scorer": best_player("goals"),
        "top_mvp": best_player("mvp"),
        "top_attendance": best_player("games"),
        "top_reputation": best_player("reputation_score"),
        "most_disciplined": None,
        "average_skill": round(sum(skill_values) / len(skill_values), 2) if skill_values else 0.0,
        "skill_distribution": skill_distribution,
        "players_count": len(players),
    }
