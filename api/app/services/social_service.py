from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.communication_utils import create_group_activity, create_notification, notification_allowed
from app.avatars_routes import resolve_avatar_url
from app.core.time import utc_now
from app.models import GroupInvite, Match, MatchParticipant, Player, PlayerNetwork, PlayerProfile, User
from app.permissions import get_group_member
from app.repositories.social import (
    count_group_invites_for_user,
    get_player,
    get_player_network_link,
    get_player_owner,
    get_player_profile,
    list_player_finished_participations,
    list_player_groups,
    list_player_participations,
    list_public_nearby_matches,
)


def safe_name(user: Optional[User], player: Optional[Player] = None) -> str:
    if user:
        return (user.name or user.email or f'Usuário {user.id}').strip()
    if player:
        return (player.name or f'Jogador {player.id}').strip()
    return 'Jogador'


def ensure_profile(db: Session, player: Player) -> PlayerProfile:
    profile = get_player_profile(db, player_id=player.id)
    if profile:
        return profile
    user = get_player_owner(db, player)
    profile = PlayerProfile(player_id=player.id, bio=None, city=(getattr(user, 'current_city', None) if user else None), avatar_url=(getattr(user, 'avatar_url', None) if user else None), main_position=(player.position or getattr(user, 'position', None) if user else player.position), skill_level=max(1, min(5, int((player.rating or 3) or 3))))
    db.add(profile)
    db.flush()
    return profile


def draw_team_number(match: Match, player_id: int) -> Optional[int]:
    for team in (match.draw_teams or []):
        for item in (team.players or []):
            if isinstance(item, dict) and item.get('kind') == 'player' and int(item.get('player_id') or 0) == player_id:
                return int(team.team_number)
    return None


def score(match: Match) -> tuple[int, int]:
    team1 = 0
    team2 = 0
    for event in (match.events or []):
        if (event.event_type or '') != 'goal':
            continue
        if int(event.team_number or 0) == 1:
            team1 += 1
        elif int(event.team_number or 0) == 2:
            team2 += 1
    return team1, team2


def player_finished_matches(db: Session, *, player_id: int) -> list[Match]:
    rows = list_player_finished_participations(db, player_id=player_id)
    return [r.match for r in rows if r.match]


def player_stats(db: Session, player: Player) -> dict:
    matches = player_finished_matches(db, player_id=player.id)
    goals = assists = wins = draws = losses = mvps = 0
    no_shows = sum(1 for row in list_player_participations(db, player_id=player.id) if bool(getattr(row, 'no_show', False)))
    for match in matches:
        team1, team2 = score(match)
        team_number = draw_team_number(match, player.id)
        if team_number == 1:
            wins += int(team1 > team2)
            losses += int(team1 < team2)
            draws += int(team1 == team2)
        elif team_number == 2:
            wins += int(team2 > team1)
            losses += int(team2 < team1)
            draws += int(team2 == team1)
        goals += sum(1 for e in (match.events or []) if e.event_type == 'goal' and e.player_id == player.id)
        assists += sum(1 for e in (match.events or []) if e.event_type == 'assist' and e.player_id == player.id)
        if int(getattr(match, 'mvp_player_id', 0) or 0) == player.id:
            mvps += 1
    games = len(matches)
    return {'matches_played': games, 'wins': wins, 'draws': draws, 'losses': losses, 'goals': goals, 'assists': assists, 'mvp': mvps, 'win_rate': round((wins / games) * 100, 1) if games else 0.0, 'unjustified_absences': no_shows}


def groups_for_player(db: Session, *, player_id: int) -> list[dict]:
    rows = list_player_groups(db, player_id=player_id)
    return [{'id': g.id, 'name': g.name} for _, g in rows]


def profile_payload(db: Session, player: Player, *, include_groups: bool = False) -> dict:
    user = get_player_owner(db, player)
    profile = ensure_profile(db, player)
    payload = {'player_id': player.id, 'user_id': player.owner_id, 'name': safe_name(user, player), 'position': profile.main_position or player.position or getattr(user, 'position', None), 'skill_level': int(profile.skill_level or max(1, min(5, int((player.rating or 3) or 3)))), 'city': profile.city or getattr(user, 'current_city', None), 'birth_city': getattr(user, 'birth_city', None), 'birth_state': getattr(user, 'birth_state', None), 'birth_country': getattr(user, 'birth_country', None), 'birth_date': getattr(user, 'birth_date', None), 'preferred_foot': getattr(player, 'preferred_foot', None) or getattr(user, 'preferred_foot', None), 'bio': profile.bio, 'avatar_url': resolve_avatar_url(profile.avatar_url or getattr(user, 'avatar_url', None)), 'stats': player_stats(db, player)}
    if include_groups:
        payload['groups'] = groups_for_player(db, player_id=player.id)
    return payload


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return r * c


def reputation(db: Session, player: Player):
    stats = player_stats(db, player)
    score_value = max(0, min(100, 65 + (stats['wins'] * 1.5) + (stats['goals'] * 0.8) + (stats['assists'] * 0.5) + (stats['mvp'] * 2) - (stats['unjustified_absences'] * 4)))
    return {'score': round(score_value, 1), 'matches_played': stats['matches_played'], 'unjustified_absences': stats['unjustified_absences'], 'invited_groups_count': count_group_invites_for_user(db, user_id=player.owner_id)}


def get_network(db: Session, *, player_id: int, limit: int):
    counters: dict[int, dict] = {}
    rows = db.query(MatchParticipant).filter(MatchParticipant.player_id == player_id).all()
    for row in rows:
        match = db.query(Match).filter(Match.id == row.match_id, Match.status == 'finished').first()
        if not match:
            continue
        others = db.query(MatchParticipant).filter(MatchParticipant.match_id == match.id, MatchParticipant.player_id != player_id).all()
        for other in others:
            entry = counters.setdefault(other.player_id, {'shared_matches': 0, 'last_played_at': None})
            entry['shared_matches'] += 1
            dt = match.starts_at
            if entry['last_played_at'] is None or (dt and dt > entry['last_played_at']):
                entry['last_played_at'] = dt
    out = []
    for other_player_id, info in sorted(counters.items(), key=lambda kv: (-kv[1]['shared_matches'], kv[0]))[:limit]:
        other = get_player(db, player_id=other_player_id)
        if not other:
            continue
        other_user = get_player_owner(db, other)
        other_profile = ensure_profile(db, other)
        invite_count = count_group_invites_for_user(db, user_id=other.owner_id)
        rep = reputation(db, other)
        existing = get_player_network_link(db, player_id=player_id, connected_player_id=other_player_id)
        if existing:
            existing.shared_matches_count = int(info['shared_matches'])
            existing.last_played_at = info['last_played_at']
        else:
            db.add(PlayerNetwork(player_id=player_id, connected_player_id=other_player_id, shared_matches_count=int(info['shared_matches']), invited_count=invite_count, last_played_at=info['last_played_at']))
        out.append({'player_id': other.id, 'user_id': other.owner_id, 'name': safe_name(other_user, other), 'position': other_profile.main_position or other.position or getattr(other_user, 'position', None), 'avatar_url': resolve_avatar_url(other_profile.avatar_url or getattr(other_user, 'avatar_url', None)), 'city': other_profile.city or getattr(other_user, 'current_city', None), 'shared_matches': int(info['shared_matches']), 'invited_groups_count': invite_count, 'last_played_at': info['last_played_at'], 'reputation_score': rep['score']})
    db.commit()
    return out


def invite_player_to_group(db: Session, *, group_id: str, current_user_id: int, member_player_id: int, player_id: int, group_name: str):
    player = get_player(db, player_id=player_id)
    if not player:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    if int(player.owner_id) == int(current_user_id):
        raise HTTPException(status_code=400, detail='Você já faz parte da sua própria conta')
    from app.models import GroupMember
    existing_member = db.query(GroupMember).filter(GroupMember.group_id == group_id, GroupMember.user_id == player.owner_id, GroupMember.status == 'active').first()
    if existing_member:
        raise HTTPException(status_code=400, detail='Jogador já participa do grupo')
    existing_invite = db.query(GroupInvite).filter(GroupInvite.group_id == group_id, GroupInvite.invited_user_id == player.owner_id, GroupInvite.status == 'pending').first()
    if existing_invite:
        return {'ok': True, 'invite_id': existing_invite.id, 'status': existing_invite.status}
    invited_user = get_player_owner(db, player)
    item = GroupInvite(group_id=group_id, invited_by_user_id=current_user_id, invited_user_id=player.owner_id, email=getattr(invited_user, 'email', None), username=safe_name(invited_user, player), status='pending')
    db.add(item)
    db.flush()
    if notification_allowed(db, player.owner_id, 'invites'):
        create_notification(db, user_id=player.owner_id, type='player_group_invite', title='Convite para grupo', message=f'Você foi convidado para o grupo {group_name}', payload={'group_id': group_id, 'invite_id': item.id, 'player_id': player_id})
    create_group_activity(db, group_id=group_id, activity_type='player_invited', title='Jogador convidado', description=f'{safe_name(invited_user, player)} foi convidado para o grupo.', actor_user_id=current_user_id, actor_player_id=member_player_id, target_user_id=player.owner_id, metadata={'invite_id': item.id, 'player_id': player_id})
    db.commit()
    return {'ok': True, 'invite_id': item.id, 'status': item.status}


def nearby_matches(db: Session, *, lat: float, lng: float, radius_km: float, limit: int):
    now = utc_now()
    rows = list_public_nearby_matches(db, now=now)
    out = []
    from app.models import Group
    for match in rows:
        d = haversine_km(lat, lng, float(match.location_lat), float(match.location_lng))
        if d > radius_km:
            continue
        group = db.query(Group).filter(Group.id == match.group_id).first() if match.group_id else None
        confirmed = db.query(MatchParticipant).filter(MatchParticipant.match_id == match.id, MatchParticipant.status == 'confirmed').count()
        available = max(0, int(match.player_limit) - confirmed) if int(match.player_limit or 0) > 0 else None
        starts_at = match.starts_at
        starts_in_minutes = None
        is_today = False
        if starts_at is not None:
            try:
                starts_in_minutes = max(0, int((starts_at - now).total_seconds() // 60))
                is_today = starts_at.date() == now.date()
            except Exception:
                starts_in_minutes = None
                is_today = False
        distance_km = round(d, 1)
        out.append({
            'match_id': match.id,
            'title': match.title or (group.name if group else f'Partida {match.id}'),
            'starts_at': starts_at,
            'starts_in_minutes': starts_in_minutes,
            'is_today': is_today,
            'distance_km': distance_km,
            'distance_label': f"{distance_km:.1f} km",
            'venue_name': match.venue_name,
            'location_name': match.location_name,
            'group_id': match.group_id,
            'group_name': group.name if group else 'Grupo',
            'available_spots': available,
            'city': match.city,
            'location_lat': float(match.location_lat),
            'location_lng': float(match.location_lng),
        })

    out.sort(key=lambda item: (float(item.get('distance_km') or 0), item.get('starts_at') or now, int(item.get('match_id') or 0)))
    return out[:limit]
