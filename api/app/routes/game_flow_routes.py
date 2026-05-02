from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.communication_utils import create_notification
from app.db import get_db
from app.models import (
    SocialFeedEvent,
    Notification,
    GroupMember,
    Match,
    MatchDrawTeam,
    MatchEvent,
    MatchGuestPlayer,
    MatchParticipant,
    MatchStatus,
    Player,
)
from app.permissions import get_group_member
from app.security import get_current_user

router = APIRouter(tags=["Groups - Game Flow"])


def _ensure_group_admin(db: Session, group_id: str, current_user_id: int) -> GroupMember:
    member = get_group_member(db, group_id, current_user_id)
    role = (getattr(member, "role", "") or "").lower()
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only owner/admin can perform this action")
    return member


def _normalize_match_status(status: str | None) -> str:
    raw = (status or "").lower()
    if raw == "canceled":
        return "cancelled"
    return raw


def _team_players_map(draw_teams: list[MatchDrawTeam]) -> dict[int, int]:
    team_by_player: dict[int, int] = {}
    for dt in draw_teams:
        for item in (dt.players or []):
            if isinstance(item, dict) and item.get("kind") == "player" and item.get("player_id"):
                team_by_player[int(item["player_id"])] = int(dt.team_number)
    return team_by_player


def _team_guests_map(draw_teams: list[MatchDrawTeam]) -> dict[int, int]:
    team_by_guest: dict[int, int] = {}
    for dt in draw_teams:
        for item in (dt.players or []):
            if isinstance(item, dict) and item.get("kind") == "guest" and item.get("guest_id"):
                team_by_guest[int(item["guest_id"])] = int(dt.team_number)
    return team_by_guest


def _player_name_map(db: Session, group_id: str) -> dict[int, str]:
    members = db.query(GroupMember).filter(GroupMember.group_id == group_id).all()
    player_ids = [gm.player_id for gm in members if getattr(gm, "player_id", None)]
    if not player_ids:
        return {}
    players = db.query(Player).filter(Player.id.in_(player_ids)).all()
    return {p.id: p.name for p in players}


def _guest_name_map(db: Session, match_id: int) -> dict[int, str]:
    guests = db.query(MatchGuestPlayer).filter(MatchGuestPlayer.match_id == match_id).all()
    return {g.id: g.name for g in guests}


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


@router.post("/groups/{group_id}/matches/{match_id}/start")
def start_match(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _ensure_group_admin(db, group_id, current_user_id)

    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    status = _normalize_match_status(match.status)
    if status in {"finished", "cancelled"}:
        raise HTTPException(status_code=400, detail="Cannot start a finished/cancelled match")

    match.status = MatchStatus.in_progress.value if hasattr(MatchStatus, "in_progress") else "in_progress"
    db.commit()
    db.refresh(match)

    return {
        "match_id": match.id,
        "status": match.status,
        "starts_at": match.starts_at.isoformat() if match.starts_at else None,
        "ends_at": match.ends_at.isoformat() if getattr(match, "ends_at", None) else None,
    }


@router.post("/groups/{group_id}/matches/{match_id}/goal")
def register_goal(
    group_id: str,
    match_id: int,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _ensure_group_admin(db, group_id, current_user_id)

    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    status = _normalize_match_status(match.status)
    if status != "in_progress":
        raise HTTPException(status_code=400, detail="Match must be in progress")

    team_number = int(payload.get("team") or payload.get("team_number") or 0)
    player_id = payload.get("player_id")
    guest_id = payload.get("guest_id")
    minute = payload.get("minute")

    if team_number not in {1, 2}:
        raise HTTPException(status_code=400, detail="team must be 1 or 2")

    if not player_id and not guest_id:
        raise HTTPException(status_code=400, detail="player_id or guest_id is required")

    event = MatchEvent(
        group_id=group_id,
        match_id=match_id,
        team_number=team_number,
        player_id=player_id,
        guest_id=guest_id,
        event_type="goal",
        minute=minute,
    )
    db.add(event)
    db.commit()

    return _match_score_payload(db, match)


@router.get("/groups/{group_id}/matches/{match_id}/score")
def get_match_score(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)

    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return _match_score_payload(db, match)


@router.post("/groups/{group_id}/matches/{match_id}/finish")
def finish_match(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _ensure_group_admin(db, group_id, current_user_id)

    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    status = _normalize_match_status(match.status)
    if status == "cancelled":
        raise HTTPException(status_code=400, detail="Cancelled match cannot be finished")

    match.status = MatchStatus.finished.value if hasattr(MatchStatus, "finished") else "finished"
    if hasattr(match, "ends_at") and not getattr(match, "ends_at", None):
        match.ends_at = utc_now()

    participants = db.query(MatchParticipant).filter(MatchParticipant.match_id == match.id).all()
    member_rows = db.query(GroupMember).filter(GroupMember.group_id == group_id, GroupMember.status == 'active').all()
    member_player_ids = {m.player_id for m in member_rows}
    external_players = [p for p in participants if p.player_id not in member_player_ids]
    if external_players:
        for member in member_rows:
            for ext in external_players:
                ext_player = db.query(Player).filter(Player.id == ext.player_id).first()
                if not ext_player:
                    continue
                external_key = f"review_prompt:{match.id}:{member.user_id}:{ext.player_id}"
                create_notification(
                    db,
                    user_id=member.user_id,
                    type='review_player_prompt',
                    title='Avalie jogador da partida',
                    message=f'Avalie {ext_player.name or f"Jogador {ext.player_id}"} pela partida concluída.',
                    external_key=external_key,
                    payload={'match_id': match.id, 'group_id': group_id, 'player_id': ext.player_id},
                )
    db.add(SocialFeedEvent(event_type='match_finished', group_id=group_id, match_id=match.id, metadata_json={'title': 'Partida concluída', 'subtitle': 'Uma partida pública foi finalizada.' if getattr(match, 'is_public', False) else 'Uma partida foi finalizada.'}))
    db.commit()
    db.refresh(match)

    return {
        "match_id": match.id,
        "status": match.status,
        "ends_at": match.ends_at.isoformat() if getattr(match, "ends_at", None) else None,
        "score": _match_score_payload(db, match),
    }


@router.post("/groups/{group_id}/matches/{match_id}/cancel")
def cancel_match(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _ensure_group_admin(db, group_id, current_user_id)

    match = db.query(Match).filter(Match.group_id == group_id, Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    status = _normalize_match_status(match.status)
    if status != "scheduled":
        raise HTTPException(status_code=400, detail="Only scheduled matches can be cancelled")

    match.status = "cancelled"
    db.commit()
    db.refresh(match)

    return {
        "match_id": match.id,
        "status": match.status,
    }
