from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Group, GroupJoinRequest, GroupMember


def get_group_or_none(db: Session, group_id: str) -> Optional[Group]:
    return db.query(Group).filter(Group.id == group_id).first()


def list_active_group_members(db: Session, group_id: str):
    return (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.status == "active")
        .all()
    )


def list_group_members_with_relations(db: Session, group_id: str):
    return (
        db.query(GroupMember)
        .options(joinedload(GroupMember.user), joinedload(GroupMember.player))
        .filter(GroupMember.group_id == group_id, GroupMember.status == "active")
        .order_by(GroupMember.created_at.asc(), GroupMember.id.asc())
        .all()
    )


def get_group_member_by_user_id(db: Session, group_id: str, user_id: int) -> Optional[GroupMember]:
    return (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        .order_by(GroupMember.id.asc())
        .first()
    )


def get_group_member_by_player_id(db: Session, group_id: str, player_id: int) -> Optional[GroupMember]:
    return (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.player_id == player_id)
        .order_by(GroupMember.id.asc())
        .first()
    )


def list_pending_join_requests(db: Session, group_id: str):
    return (
        db.query(GroupJoinRequest)
        .options(joinedload(GroupJoinRequest.user), joinedload(GroupJoinRequest.player))
        .filter(GroupJoinRequest.group_id == group_id, GroupJoinRequest.status == "pending")
        .order_by(GroupJoinRequest.created_at.asc(), GroupJoinRequest.id.asc())
        .all()
    )


def get_join_request_by_id(db: Session, group_id: str, request_id: int) -> Optional[GroupJoinRequest]:
    return (
        db.query(GroupJoinRequest)
        .filter(GroupJoinRequest.group_id == group_id, GroupJoinRequest.id == request_id)
        .first()
    )


def get_join_request_by_group_and_player(db: Session, group_id: str, player_id: int) -> Optional[GroupJoinRequest]:
    return (
        db.query(GroupJoinRequest)
        .filter(GroupJoinRequest.group_id == group_id, GroupJoinRequest.player_id == player_id)
        .first()
    )
