from __future__ import annotations

from datetime import date, datetime
import logging
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.communication_utils import create_group_activity, create_notification
from app.avatars_routes import resolve_avatar_url
from app.core.time import utc_now
from app.core.logging import log_event
from app.db import get_db
from app.models import (
    Friendship,
    Group,
    GroupMember,
    GroupRating,
    Match,
    MatchParticipant,
    Notification,
    Player,
    PlayerNetwork,
    PlayerProfile,
    PlayerRating,
    SocialFeedEvent,
    SocialPost,
    SocialPostComment,
    SocialPostLike,
    User,
)
from app.permissions import get_group_member, get_user_primary_player
from app.security import get_current_user
from app.services.social_service import (
    get_network,
    get_player,
    get_player_owner,
    invite_player_to_group as service_invite_player_to_group,
    nearby_matches,
    player_finished_matches,
    profile_payload,
)

router = APIRouter(tags=["social"])
logger = logging.getLogger(__name__)


def _safe_name(user: Optional[User], player: Optional[Player] = None) -> str:
    if user:
        return (user.name or user.email or f"Usuário {user.id}").strip()
    if player:
        return (player.name or f"Jogador {player.id}").strip()
    return "Jogador"


def _player_user(db: Session, player: Optional[Player]) -> Optional[User]:
    if not player:
        return None
    return db.query(User).filter(User.id == player.owner_id).first()


def _player_profile(db: Session, player_id: int) -> Optional[PlayerProfile]:
    return db.query(PlayerProfile).filter(PlayerProfile.player_id == player_id).first()


def _shared_active_groups(db: Session, a_player_id: int, b_player_id: int) -> list[str]:
    a = db.query(GroupMember).filter(GroupMember.player_id == a_player_id, GroupMember.status == 'active').all()
    if not a:
        return []
    group_ids = [item.group_id for item in a]
    b = db.query(GroupMember).filter(GroupMember.player_id == b_player_id, GroupMember.status == 'active', GroupMember.group_id.in_(group_ids)).all()
    return [item.group_id for item in b]


def _ensure_profile(db: Session, player: Player) -> PlayerProfile:
    profile = _player_profile(db, player.id)
    if profile:
        return profile
    user = _player_user(db, player)
    profile = PlayerProfile(
        player_id=player.id,
        bio=None,
        city=(getattr(user, "current_city", None) if user else None),
        avatar_url=(getattr(user, "avatar_url", None) if user else None),
        main_position=(player.position or getattr(user, "position", None) if user else player.position),
        skill_level=max(1, min(5, int((player.rating or 3) or 3))),
    )
    db.add(profile)
    db.flush()
    return profile


def _rating_summary_dict(rows: list[PlayerRating]) -> dict:
    if not rows:
        return {
            'average': 0.0,
            'count': 0,
            'skill_average': 0.0,
            'fair_play_average': 0.0,
            'commitment_average': 0.0,
            'reputation_score': None,
            'total_reviews': 0,
        }
    skill_avg = round(sum(r.skill for r in rows) / len(rows), 1)
    fair_avg = round(sum(r.fair_play for r in rows) / len(rows), 1)
    commitment_avg = round(sum(r.commitment for r in rows) / len(rows), 1)
    avg = round((skill_avg + fair_avg + commitment_avg) / 3, 1)
    return {
        'average': avg,
        'count': len(rows),
        'skill_average': skill_avg,
        'fair_play_average': fair_avg,
        'commitment_average': commitment_avg,
        'reputation_score': avg,
        'total_reviews': len(rows),
    }


def _reputation(db: Session, player: Player) -> dict:
    rows = db.query(PlayerRating).filter(PlayerRating.rated_player_id == player.id).all()
    summary = _rating_summary_dict(rows)
    avg = summary['reputation_score']
    return {
        'player_id': player.id,
        'score': avg,
        'label': 'Sem reputação' if avg is None else f"{avg:.1f} ★",
        'components': {
            'skill': summary['skill_average'],
            'fair_play': summary['fair_play_average'],
            'commitment': summary['commitment_average'],
            'total_reviews': summary['total_reviews'],
        },
    }


def _build_feed_event_payload(db: Session, event: SocialFeedEvent) -> dict:
    actor = db.query(Player).filter(Player.id == event.actor_player_id).first() if event.actor_player_id else None
    target = db.query(Player).filter(Player.id == event.target_player_id).first() if event.target_player_id else None
    actor_user = _player_user(db, actor)
    target_user = _player_user(db, target)
    actor_profile = _player_profile(db, actor.id) if actor else None
    group = db.query(Group).filter(Group.id == event.group_id).first() if event.group_id else None
    meta = dict(event.metadata_json or {})
    title = meta.get('title')
    subtitle = meta.get('subtitle')
    if not title:
        if event.event_type == 'friendship_accepted':
            title = 'Nova amizade'
            subtitle = f"{_safe_name(actor_user, actor)} e {_safe_name(target_user, target)} agora são amigos."
        elif event.event_type == 'player_review_received':
            title = 'Jogador avaliado'
            subtitle = f"{_safe_name(target_user, target)} recebeu nova avaliação."
        elif event.event_type == 'match_finished':
            title = 'Partida concluída'
            subtitle = f"{group.name if group else 'Grupo'} concluiu uma partida."
        else:
            title = 'Atividade recente'
            subtitle = meta.get('subtitle') or (group.name if group else 'BoraFut')
    return {
        'id': event.id,
        'event_type': event.event_type,
        'title': title,
        'subtitle': subtitle,
        'created_at': event.created_at,
        'actor_player_id': event.actor_player_id,
        'actor_name': _safe_name(actor_user, actor) if actor else None,
        'actor_avatar_url': resolve_avatar_url(actor_profile.avatar_url if actor_profile else getattr(actor_user, 'avatar_url', None)),
        'target_player_id': event.target_player_id,
        'target_name': _safe_name(target_user, target) if target else None,
        'group_id': event.group_id,
        'group_name': group.name if group else None,
        'match_id': event.match_id,
        'metadata': meta,
    }


def _create_feed_event(db: Session, *, event_type: str, actor_player_id: int | None = None, target_player_id: int | None = None, group_id: str | None = None, match_id: int | None = None, metadata: dict | None = None) -> None:
    db.add(SocialFeedEvent(
        event_type=event_type,
        actor_player_id=actor_player_id,
        target_player_id=target_player_id,
        group_id=group_id,
        match_id=match_id,
        metadata_json=metadata or {},
    ))



def _validate_geo_query(*, lat: float, lng: float, radius_km: float) -> None:
    if lat < -90 or lat > 90:
        raise HTTPException(status_code=400, detail='Latitude inválida para busca de partidas próximas')
    if lng < -180 or lng > 180:
        raise HTTPException(status_code=400, detail='Longitude inválida para busca de partidas próximas')
    if radius_km <= 0 or radius_km > 50:
        raise HTTPException(status_code=400, detail='O raio de busca deve estar entre 1 e 50 km')

def _friend_player_ids(db: Session, me: Player) -> list[int]:
    ids = [
        f.addressee_player_id
        for f in db.query(Friendship).filter(Friendship.requester_player_id == me.id, Friendship.status == 'accepted').all()
    ]
    ids += [
        f.requester_player_id
        for f in db.query(Friendship).filter(Friendship.addressee_player_id == me.id, Friendship.status == 'accepted').all()
    ]
    return sorted({int(x) for x in ids if x})


def _serialize_post_content(*, post_type: str, text_value: str | None = None, snapshot: dict | None = None) -> str:
    return json.dumps({
        'post_type': post_type,
        'text': (text_value or '').strip(),
        'snapshot': snapshot or {},
    }, ensure_ascii=False)


def _parse_post_content(raw: str | None) -> dict:
    raw = (raw or '').strip()
    if not raw:
        return {'post_type': 'text', 'text': '', 'snapshot': {}}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {
                'post_type': str(data.get('post_type') or 'text'),
                'text': str(data.get('text') or ''),
                'snapshot': dict(data.get('snapshot') or {}),
            }
    except Exception:
        pass
    return {'post_type': 'text', 'text': raw, 'snapshot': {}}


def _build_post_payload(db: Session, post: SocialPost, current_player_id: int | None = None) -> dict:
    author = db.query(Player).filter(Player.id == post.player_id).first()
    author_user = _player_user(db, author)
    author_profile = _player_profile(db, post.player_id) if author else None
    parsed = _parse_post_content(post.content)
    comments_payload = []
    comments = db.query(SocialPostComment).filter(SocialPostComment.post_id == post.id).order_by(SocialPostComment.created_at.asc(), SocialPostComment.id.asc()).all()
    for item in comments:
        comment_player = db.query(Player).filter(Player.id == item.player_id).first()
        comment_user = _player_user(db, comment_player)
        comment_profile = _player_profile(db, item.player_id) if comment_player else None
        comments_payload.append({
            'id': item.id,
            'player_id': item.player_id,
            'player_name': _safe_name(comment_user, comment_player),
            'player_avatar_url': resolve_avatar_url(comment_profile.avatar_url if comment_profile else getattr(comment_user, 'avatar_url', None)),
            'comment': item.comment,
            'created_at': item.created_at,
        })
    like_rows = db.query(SocialPostLike).filter(SocialPostLike.post_id == post.id).all()
    liked_by_me = any(int(row.player_id) == int(current_player_id or 0) for row in like_rows)
    return {
        'id': post.id,
        'player_id': post.player_id,
        'player_name': _safe_name(author_user, author),
        'player_avatar_url': resolve_avatar_url(author_profile.avatar_url if author_profile else getattr(author_user, 'avatar_url', None)),
        'content': parsed.get('text') or '',
        'post_type': parsed.get('post_type') or 'text',
        'snapshot': dict(parsed.get('snapshot') or {}),
        'likes_count': len(like_rows),
        'comments_count': len(comments_payload),
        'liked_by_me': liked_by_me,
        'comments': comments_payload,
        'created_at': post.created_at,
    }



def _external_public_match_allowed(db: Session, me: Player, target: Player, match_id: int) -> tuple[Match, str]:
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or not match.group_id:
        raise HTTPException(status_code=404, detail='Partida não encontrada')
    if (match.status or '') != 'finished':
        raise HTTPException(status_code=400, detail='A partida precisa estar finalizada')
    if not bool(getattr(match, 'is_public', False)):
        raise HTTPException(status_code=400, detail='Esta avaliação pós-jogo só é válida para partida pública')
    me_member = db.query(GroupMember).filter(GroupMember.group_id == match.group_id, GroupMember.player_id == me.id, GroupMember.status == 'active').first()
    if not me_member:
        raise HTTPException(status_code=403, detail='Apenas membros do grupo podem avaliar jogadores externos')
    target_member = db.query(GroupMember).filter(GroupMember.group_id == match.group_id, GroupMember.player_id == target.id, GroupMember.status == 'active').first()
    if target_member:
        raise HTTPException(status_code=400, detail='Jogador é membro do grupo; use a avaliação normal do grupo')
    me_part = db.query(MatchParticipant).filter(MatchParticipant.match_id == match.id, MatchParticipant.player_id == me.id).first()
    target_part = db.query(MatchParticipant).filter(MatchParticipant.match_id == match.id, MatchParticipant.player_id == target.id).first()
    if not me_part or not target_part:
        raise HTTPException(status_code=400, detail='Ambos precisam ter participado da partida')
    return match, match.group_id


class StatsOut(BaseModel):
    matches_played: int
    wins: int
    draws: int
    losses: int
    goals: int
    assists: int
    mvp: int
    win_rate: float
    unjustified_absences: int


class PlayerProfileOut(BaseModel):
    player_id: int
    user_id: int
    name: str
    position: Optional[str] = None
    skill_level: int
    city: Optional[str] = None
    birth_city: Optional[str] = None
    birth_state: Optional[str] = None
    birth_country: Optional[str] = None
    birth_date: Optional[date] = None
    preferred_foot: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    stats: StatsOut
    groups: Optional[list[dict]] = None


class PlayerHistoryOut(BaseModel):
    match_id: int
    date_time: datetime
    group_id: Optional[str] = None
    group_name: str
    result: str
    goals: int
    assists: int
    mvp: bool
    team_number: Optional[int] = None
    title: Optional[str] = None


class NetworkPlayerOut(BaseModel):
    player_id: int
    user_id: int
    name: str
    position: Optional[str] = None
    avatar_url: Optional[str] = None
    city: Optional[str] = None
    shared_matches: int
    invited_groups_count: int
    last_played_at: Optional[datetime] = None
    reputation_score: float | None = None


class InvitePlayerIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    player_id: int = Field(..., gt=0)


class ReputationOut(BaseModel):
    player_id: int
    score: float | None = None
    label: str
    components: dict


class NearbyMatchOut(BaseModel):
    match_id: int
    title: str
    starts_at: datetime
    starts_in_minutes: Optional[int] = None
    is_today: bool = False
    distance_km: float
    distance_label: str
    venue_name: str
    location_name: Optional[str] = None
    group_id: Optional[str] = None
    group_name: str
    available_spots: Optional[int] = None
    city: Optional[str] = None
    location_lat: float
    location_lng: float


class RatingSummaryOut(BaseModel):
    average: float
    count: int
    skill_average: Optional[float] = None
    fair_play_average: Optional[float] = None
    commitment_average: Optional[float] = None
    organization_average: Optional[float] = None
    level_average: Optional[float] = None
    reputation_score: Optional[float] = None
    total_reviews: int = 0


class PlayerRatingIn(BaseModel):
    match_id: Optional[int] = None
    group_id: Optional[str] = None
    skill: int = Field(ge=1, le=5)
    fair_play: int = Field(ge=1, le=5)
    commitment: int = Field(ge=1, le=5)


class FeedEventOut(BaseModel):
    id: int
    event_type: str
    title: str
    subtitle: Optional[str] = None
    created_at: datetime
    actor_player_id: Optional[int] = None
    actor_name: Optional[str] = None
    actor_avatar_url: Optional[str] = None
    target_player_id: Optional[int] = None
    target_name: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    match_id: Optional[int] = None
    metadata: dict = Field(default_factory=dict)


class SocialPostCommentIn(BaseModel):
    comment: str = Field(..., min_length=1, max_length=300)


class SocialPostCreateIn(BaseModel):
    content: Optional[str] = Field(default=None, max_length=1000)
    post_type: str = Field(default='text', pattern='^(text|stats|fifa_card)$')


class SocialPostCommentOut(BaseModel):
    id: int
    player_id: int
    player_name: str
    player_avatar_url: Optional[str] = None
    comment: str
    created_at: datetime


class SocialPostOut(BaseModel):
    id: int
    player_id: int
    player_name: str
    player_avatar_url: Optional[str] = None
    content: str = ''
    post_type: str
    snapshot: dict = Field(default_factory=dict)
    likes_count: int = 0
    comments_count: int = 0
    liked_by_me: bool = False
    comments: list[SocialPostCommentOut] = Field(default_factory=list)
    created_at: datetime


class FriendRequestIn(BaseModel):
    player_id: int = Field(..., gt=0)


class FriendOut(BaseModel):
    friendship_id: int
    player_id: int
    name: str
    position: Optional[str] = None
    city: Optional[str] = None
    avatar_url: Optional[str] = None
    status: str
    requested_at: datetime


@router.get('/players/{player_id}/profile', response_model=PlayerProfileOut)
def get_player_profile(player_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    if player.owner_id != current_user_id:
        current_player = get_user_primary_player(db, current_user_id)
        shared = db.query(PlayerNetwork).filter(PlayerNetwork.player_id == current_player.id, PlayerNetwork.connected_player_id == player_id).first()
        if not shared:
            raise HTTPException(status_code=403, detail='Sem permissão para visualizar este perfil esportivo')
    return PlayerProfileOut(**profile_payload(db, player, include_groups=True))


@router.get('/players/{player_id}/public-profile', response_model=PlayerProfileOut)
def get_player_public_profile(player_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _ = current_user_id
    player = get_player(db, player_id=player_id)
    if not player:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    return PlayerProfileOut(**profile_payload(db, player, include_groups=True))


@router.get('/players/{player_id}/matches-history', response_model=list[PlayerHistoryOut])
def get_player_matches_history(player_id: int, limit: int = Query(default=30, ge=1, le=100), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _ = current_user_id
    player = get_player(db, player_id=player_id)
    if not player:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    matches = player_finished_matches(db, player_id=player_id)[:limit]
    out = []
    for match in matches:
        group = db.query(Group).filter(Group.id == match.group_id).first() if match.group_id else None
        out.append(PlayerHistoryOut(
            match_id=match.id,
            date_time=match.starts_at,
            group_id=match.group_id,
            group_name=group.name if group else 'Grupo',
            result='played',
            goals=0,
            assists=0,
            mvp=int(getattr(match, 'mvp_player_id', 0) or 0) == player_id,
            team_number=None,
            title=match.title,
        ))
    return out


@router.get('/players/{player_id}/network', response_model=list[NetworkPlayerOut])
def get_player_network(player_id: int, limit: int = Query(default=30, ge=1, le=100), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    player = get_player(db, player_id=player_id)
    if not player:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    if player.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail='A rede só pode ser consultada pelo próprio jogador')
    return [NetworkPlayerOut(**item) for item in get_network(db, player_id=player_id, limit=limit)]


@router.post('/groups/{group_id}/invite-player', status_code=201)
def invite_player_to_group(group_id: str, payload: InvitePlayerIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    group, member = get_group_member(db, group_id, current_user_id)
    return service_invite_player_to_group(db, group_id=group_id, current_user_id=current_user_id, member_player_id=member.player_id, player_id=payload.player_id, group_name=group.name)


@router.get('/players/{player_id}/reputation', response_model=ReputationOut)
def get_player_reputation(player_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _ = current_user_id
    player = get_player(db, player_id=player_id)
    if not player:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    return ReputationOut(**_reputation(db, player))


@router.get('/matches/nearby', response_model=list[NearbyMatchOut])
def get_matches_nearby(lat: float = Query(...), lng: float = Query(...), radius_km: float = Query(default=10, gt=0, le=50), limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _validate_geo_query(lat=lat, lng=lng, radius_km=radius_km)
    items = nearby_matches(db, lat=lat, lng=lng, radius_km=radius_km, limit=limit)
    log_event(logger, 'nearby_matches_search', user_id=current_user_id, radius_km=radius_km, limit=limit, result_count=len(items))
    return [NearbyMatchOut(**item) for item in items]


@router.get('/feed', response_model=list[FeedEventOut])
def get_feed(limit: int = Query(40, ge=1, le=100), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    try:
        friend_ids = _friend_player_ids(db, me)
        posts = db.query(SocialFeedEvent).filter(
            (SocialFeedEvent.actor_player_id == me.id)
            | (SocialFeedEvent.target_player_id == me.id)
            | (SocialFeedEvent.actor_player_id.in_(friend_ids) if friend_ids else False)
            | (SocialFeedEvent.target_player_id.in_(friend_ids) if friend_ids else False)
        ).order_by(SocialFeedEvent.created_at.desc(), SocialFeedEvent.id.desc()).limit(limit).all()
    except (ProgrammingError, OperationalError):
        db.rollback()
        return []
    return [_build_feed_event_payload(db, item) for item in posts]


@router.get('/posts', response_model=list[SocialPostOut])
def list_social_posts(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    friend_ids = _friend_player_ids(db, me)
    player_ids = [me.id] + friend_ids
    rows = db.query(SocialPost).filter(SocialPost.player_id.in_(player_ids)).order_by(SocialPost.created_at.desc(), SocialPost.id.desc()).limit(limit).all()
    return [_build_post_payload(db, row, current_player_id=me.id) for row in rows]


@router.post('/posts', response_model=SocialPostOut, status_code=201)
def create_social_post(payload: SocialPostCreateIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    player_profile_payload = profile_payload(db, me, include_groups=True)
    snapshot = {}
    if payload.post_type == 'stats':
        snapshot = {
            'stats': player_profile_payload.get('stats') or {},
            'name': player_profile_payload.get('name'),
        }
    elif payload.post_type == 'fifa_card':
        snapshot = {
            'profile': player_profile_payload,
            'reputation': _reputation(db, me),
        }
    item = SocialPost(
        player_id=me.id,
        content=_serialize_post_content(post_type=payload.post_type, text_value=payload.content, snapshot=snapshot),
    )
    db.add(item)
    db.flush()
    _create_feed_event(
        db,
        event_type='social_post_created',
        actor_player_id=me.id,
        metadata={
            'title': 'Nova publicação',
            'subtitle': f'{player_profile_payload.get("name") or "Jogador"} fez uma nova publicação.',
            'post_id': item.id,
            'post_type': payload.post_type,
        },
    )
    db.commit()
    db.refresh(item)
    return _build_post_payload(db, item, current_player_id=me.id)


@router.post('/posts/{post_id}/likes', response_model=SocialPostOut)
def toggle_social_post_like(post_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail='Publicação não encontrada')
    row = db.query(SocialPostLike).filter(SocialPostLike.post_id == post_id, SocialPostLike.player_id == me.id).first()
    if row:
        db.delete(row)
    else:
        db.add(SocialPostLike(post_id=post_id, player_id=me.id))
    db.commit()
    db.refresh(post)
    return _build_post_payload(db, post, current_player_id=me.id)


@router.post('/posts/{post_id}/comments', response_model=SocialPostOut)
def create_social_post_comment(post_id: int, payload: SocialPostCommentIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    post = db.query(SocialPost).filter(SocialPost.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail='Publicação não encontrada')
    db.add(SocialPostComment(post_id=post_id, player_id=me.id, comment=payload.comment.strip()))
    db.commit()
    db.refresh(post)
    return _build_post_payload(db, post, current_player_id=me.id)


@router.get('/friends', response_model=list[FriendOut])
def list_friends(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    rows = db.query(Friendship).filter(
        ((Friendship.requester_player_id == me.id) | (Friendship.addressee_player_id == me.id)),
        Friendship.status == 'accepted'
    ).order_by(Friendship.updated_at.desc()).all()
    out = []
    for row in rows:
        other_id = row.addressee_player_id if row.requester_player_id == me.id else row.requester_player_id
        other = db.query(Player).filter(Player.id == other_id).first()
        profile = _player_profile(db, other_id) if other else None
        user = _player_user(db, other)
        out.append(FriendOut(
            friendship_id=row.id,
            player_id=other_id,
            name=_safe_name(user, other),
            position=(profile.main_position if profile else getattr(other, 'position', None)),
            city=(profile.city if profile else getattr(user, 'current_city', None)),
            avatar_url=resolve_avatar_url(profile.avatar_url if profile else getattr(user, 'avatar_url', None)),
            status=row.status,
            requested_at=row.created_at,
        ))
    return out


@router.get('/friends/requests', response_model=list[FriendOut])
def list_friend_requests(direction: str = Query('incoming', pattern='^(incoming|outgoing)$'), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    if direction == 'incoming':
        rows = db.query(Friendship).filter(Friendship.addressee_player_id == me.id, Friendship.status == 'pending').order_by(Friendship.created_at.desc()).all()
        other_getter = lambda row: row.requester_player_id
    else:
        rows = db.query(Friendship).filter(Friendship.requester_player_id == me.id, Friendship.status == 'pending').order_by(Friendship.created_at.desc()).all()
        other_getter = lambda row: row.addressee_player_id
    out = []
    for row in rows:
        other_id = other_getter(row)
        other = db.query(Player).filter(Player.id == other_id).first()
        profile = _player_profile(db, other_id) if other else None
        user = _player_user(db, other)
        out.append(FriendOut(
            friendship_id=row.id,
            player_id=other_id,
            name=_safe_name(user, other),
            position=(profile.main_position if profile else getattr(other, 'position', None)),
            city=(profile.city if profile else getattr(user, 'current_city', None)),
            avatar_url=resolve_avatar_url(profile.avatar_url if profile else getattr(user, 'avatar_url', None)),
            status=row.status,
            requested_at=row.created_at,
        ))
    return out


@router.get('/friends/search', response_model=list[FriendOut])
def search_players_for_friends(q: str = Query(..., min_length=1), limit: int = Query(20, ge=1, le=50), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    term = f"%{q.strip()}%"
    rows = db.query(Player).filter(Player.id != me.id, Player.name.ilike(term)).limit(limit).all()
    out = []
    for other in rows:
        profile = _player_profile(db, other.id)
        user = _player_user(db, other)
        rel = db.query(Friendship).filter(
            ((Friendship.requester_player_id == me.id) & (Friendship.addressee_player_id == other.id)) |
            ((Friendship.requester_player_id == other.id) & (Friendship.addressee_player_id == me.id))
        ).first()
        out.append(FriendOut(
            friendship_id=rel.id if rel else 0,
            player_id=other.id,
            name=_safe_name(user, other),
            position=(profile.main_position if profile else other.position),
            city=(profile.city if profile else getattr(user, 'current_city', None)),
            avatar_url=resolve_avatar_url(profile.avatar_url if profile else getattr(user, 'avatar_url', None)),
            status=rel.status if rel else 'none',
            requested_at=(rel.created_at if rel else utc_now()),
        ))
    return out


@router.post('/friends/requests', response_model=FriendOut, status_code=201)
def create_friend_request(payload: FriendRequestIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    if payload.player_id == me.id:
        raise HTTPException(status_code=400, detail='Não é possível adicionar a si mesmo')
    target = db.query(Player).filter(Player.id == payload.player_id).first()
    if not target:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    row = db.query(Friendship).filter(
        ((Friendship.requester_player_id == me.id) & (Friendship.addressee_player_id == target.id)) |
        ((Friendship.requester_player_id == target.id) & (Friendship.addressee_player_id == me.id))
    ).first()
    if row:
        if row.status == 'accepted':
            raise HTTPException(status_code=400, detail='Jogador já é seu amigo')
        if row.status == 'pending':
            if row.addressee_player_id == me.id:
                row.status = 'accepted'
                row.responded_at = utc_now()
            db.commit()
        else:
            row.requester_player_id = me.id
            row.addressee_player_id = target.id
            row.status = 'pending'
            row.responded_at = None
            db.commit()
        db.refresh(row)
    else:
        row = Friendship(requester_player_id=me.id, addressee_player_id=target.id, status='pending')
        db.add(row)
        db.commit()
        db.refresh(row)
    create_notification(db, user_id=target.owner_id, type='friend_request', title='Nova solicitação de amizade', message=f'{_safe_name(_player_user(db, me), me)} enviou uma solicitação.', payload={'player_id': me.id, 'friendship_id': row.id})
    db.commit()
    user = _player_user(db, target)
    profile = _player_profile(db, target.id)
    return FriendOut(friendship_id=row.id, player_id=target.id, name=_safe_name(user, target), position=(profile.main_position if profile else target.position), city=(profile.city if profile else getattr(user, 'current_city', None)), avatar_url=resolve_avatar_url(profile.avatar_url if profile else getattr(user, 'avatar_url', None)), status=row.status, requested_at=row.created_at)


@router.post('/friends/requests/{friendship_id}/accept', response_model=FriendOut)
def accept_friend_request(friendship_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    row = db.query(Friendship).filter(Friendship.id == friendship_id, Friendship.addressee_player_id == me.id, Friendship.status == 'pending').first()
    if not row:
        raise HTTPException(status_code=404, detail='Solicitação não encontrada')
    row.status = 'accepted'
    row.responded_at = utc_now()
    _create_feed_event(db, event_type='friendship_accepted', actor_player_id=row.requester_player_id, target_player_id=row.addressee_player_id, metadata={'title': 'Nova amizade'})
    db.commit()
    other = db.query(Player).filter(Player.id == row.requester_player_id).first()
    user = _player_user(db, other)
    profile = _player_profile(db, other.id) if other else None
    return FriendOut(friendship_id=row.id, player_id=other.id, name=_safe_name(user, other), position=(profile.main_position if profile else other.position if other else None), city=(profile.city if profile else getattr(user, 'current_city', None)), avatar_url=resolve_avatar_url(profile.avatar_url if profile else getattr(user, 'avatar_url', None)), status=row.status, requested_at=row.created_at)


@router.post('/friends/requests/{friendship_id}/reject', response_model=FriendOut)
def reject_friend_request(friendship_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    row = db.query(Friendship).filter(Friendship.id == friendship_id, Friendship.addressee_player_id == me.id, Friendship.status == 'pending').first()
    if not row:
        raise HTTPException(status_code=404, detail='Solicitação não encontrada')
    row.status = 'rejected'
    row.responded_at = utc_now()
    db.commit()
    other = db.query(Player).filter(Player.id == row.requester_player_id).first()
    user = _player_user(db, other)
    profile = _player_profile(db, other.id) if other else None
    return FriendOut(friendship_id=row.id, player_id=other.id, name=_safe_name(user, other), position=(profile.main_position if profile else other.position if other else None), city=(profile.city if profile else getattr(user, 'current_city', None)), avatar_url=resolve_avatar_url(profile.avatar_url if profile else getattr(user, 'avatar_url', None)), status=row.status, requested_at=row.created_at)


@router.post('/players/{player_id}/ratings', response_model=RatingSummaryOut, status_code=201)
def rate_player(player_id: int, payload: PlayerRatingIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    target = db.query(Player).filter(Player.id == player_id).first()
    if not target:
        raise HTTPException(status_code=404, detail='Jogador não encontrado')
    if int(target.id) == int(me.id):
        raise HTTPException(status_code=400, detail='Não é possível avaliar a si mesmo')

    group_id = payload.group_id
    match_id = payload.match_id
    origin = 'group_member_manual'

    shared_groups = _shared_active_groups(db, me.id, target.id)
    if shared_groups:
        group_id = group_id or shared_groups[0]
        if group_id not in shared_groups:
            raise HTTPException(status_code=400, detail='Os jogadores não compartilham este grupo')
        existing = db.query(PlayerRating).filter(
            PlayerRating.group_id == group_id,
            PlayerRating.match_id.is_(None),
            PlayerRating.rater_player_id == me.id,
            PlayerRating.rated_player_id == target.id,
            PlayerRating.review_origin == 'group_member_manual',
        ).first()
    else:
        if not match_id:
            raise HTTPException(status_code=400, detail='Informe match_id para avaliação pós-jogo de jogador externo')
        match, group_id = _external_public_match_allowed(db, me, target, match_id)
        origin = 'public_match_post_game'
        existing = db.query(PlayerRating).filter(
            PlayerRating.match_id == match.id,
            PlayerRating.rater_player_id == me.id,
            PlayerRating.rated_player_id == target.id,
        ).first()

    if existing:
        existing.skill = payload.skill
        existing.fair_play = payload.fair_play
        existing.commitment = payload.commitment
        existing.group_id = group_id
        existing.review_origin = origin
    else:
        db.add(PlayerRating(group_id=group_id, match_id=match_id, rater_player_id=me.id, rated_player_id=target.id, skill=payload.skill, fair_play=payload.fair_play, commitment=payload.commitment, review_origin=origin))
    _create_feed_event(db, event_type='player_review_received', actor_player_id=me.id, target_player_id=target.id, group_id=group_id, match_id=match_id, metadata={'title': 'Jogador avaliado', 'subtitle': f'{_safe_name(_player_user(db, target), target)} recebeu nova avaliação.'})
    create_group_activity(db, group_id=group_id, activity_type='player_review_received', title='Jogador avaliado', description=f'{_safe_name(_player_user(db, target), target)} recebeu nova avaliação.', actor_user_id=current_user_id, actor_player_id=me.id, target_user_id=target.owner_id, match_id=match_id, metadata={'rated_player_id': target.id})
    db.commit()
    return get_player_rating_summary(player_id, db, current_user_id)


@router.get('/players/{player_id}/rating-summary', response_model=RatingSummaryOut)
def get_player_rating_summary(player_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _ = current_user_id
    try:
        rows = db.query(PlayerRating).filter(PlayerRating.rated_player_id == player_id).all()
    except (ProgrammingError, OperationalError):
        db.rollback()
        return RatingSummaryOut(**_rating_summary_dict([]))
    return RatingSummaryOut(**_rating_summary_dict(rows))


@router.get('/players/me/review-prompts')
def list_review_prompts(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    items = []
    rows = db.query(Notification).filter(Notification.user_id == current_user_id, Notification.type == 'review_player_prompt').order_by(Notification.created_at.desc()).all()
    for row in rows:
        payload = dict(row.payload or {})
        items.append({'id': row.id, 'title': row.title, 'message': row.message, 'created_at': row.created_at, 'payload': payload})
    return items


class GroupRatingIn(BaseModel):
    organization: int = Field(ge=1, le=5)
    fair_play: int = Field(ge=1, le=5)
    level: int = Field(ge=1, le=5)


@router.post('/groups/{group_id}/ratings', response_model=RatingSummaryOut, status_code=201)
def rate_group(group_id: str, payload: GroupRatingIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    me = get_user_primary_player(db, current_user_id)
    _ = get_group_member(db, group_id, current_user_id)
    row = db.query(GroupRating).filter(GroupRating.group_id == group_id, GroupRating.player_id == me.id).first()
    if row:
        row.organization = payload.organization
        row.fair_play = payload.fair_play
        row.level = payload.level
    else:
        db.add(GroupRating(group_id=group_id, player_id=me.id, organization=payload.organization, fair_play=payload.fair_play, level=payload.level))
    db.commit()
    return get_group_rating_summary(group_id, db, current_user_id)


@router.get('/groups/{group_id}/rating-summary', response_model=RatingSummaryOut)
def get_group_rating_summary(group_id: str, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _ = current_user_id
    rows = db.query(GroupRating).filter(GroupRating.group_id == group_id).all()
    if not rows:
        return RatingSummaryOut(average=0.0, count=0, organization_average=0.0, fair_play_average=0.0, level_average=0.0)
    org_avg = round(sum(r.organization for r in rows) / len(rows), 1)
    fair_avg = round(sum(r.fair_play for r in rows) / len(rows), 1)
    level_avg = round(sum(r.level for r in rows) / len(rows), 1)
    avg = round((org_avg + fair_avg + level_avg) / 3, 1)
    return RatingSummaryOut(average=avg, count=len(rows), organization_average=org_avg, fair_play_average=fair_avg, level_average=level_avg)
