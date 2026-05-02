from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import (
    Group,
    GroupInvite,
    GroupMember,
    Match,
    MatchParticipant,
    Player,
    PlayerNetwork,
    PlayerProfile,
    User,
)


def get_player(db: Session, *, player_id: int):
    return db.query(Player).filter(Player.id == player_id).first()


def get_player_owner(db: Session, player: Player):
    return db.query(User).filter(User.id == player.owner_id).first()


def get_player_profile(db: Session, *, player_id: int):
    return db.query(PlayerProfile).filter(PlayerProfile.player_id == player_id).first()


def list_player_finished_participations(db: Session, *, player_id: int):
    return (
        db.query(MatchParticipant)
        .join(Match, Match.id == MatchParticipant.match_id)
        .filter(MatchParticipant.player_id == player_id)
        .filter(Match.status == 'finished')
        .order_by(Match.starts_at.desc(), Match.id.desc())
        .all()
    )


def list_player_participations(db: Session, *, player_id: int):
    return db.query(MatchParticipant).filter(MatchParticipant.player_id == player_id).all()


def list_player_groups(db: Session, *, player_id: int):
    return (
        db.query(GroupMember, Group)
        .join(Group, Group.id == GroupMember.group_id)
        .filter(GroupMember.player_id == player_id, GroupMember.status == 'active')
        .order_by(Group.name.asc())
        .all()
    )


def list_player_network_links(db: Session, *, player_id: int):
    return db.query(PlayerNetwork).filter(PlayerNetwork.player_id == player_id).all()


def get_player_network_link(db: Session, *, player_id: int, connected_player_id: int):
    return db.query(PlayerNetwork).filter(PlayerNetwork.player_id == player_id, PlayerNetwork.connected_player_id == connected_player_id).first()


def count_group_invites_for_user(db: Session, *, user_id: int) -> int:
    return db.query(GroupInvite).filter(GroupInvite.invited_user_id == user_id).count()


def list_public_nearby_matches(db: Session, *, now):
    return (
        db.query(Match)
        .filter(Match.is_public.is_(True))
        .filter(Match.status.in_(['scheduled', 'in_progress']))
        .filter(Match.starts_at >= now)
        .filter(Match.location_lat.isnot(None))
        .filter(Match.location_lng.isnot(None))
        .order_by(Match.starts_at.asc(), Match.id.asc())
        .all()
    )
