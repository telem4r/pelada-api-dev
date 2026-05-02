from __future__ import annotations

from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import (
    GroupActivityLog,
    GroupAnnouncement,
    GroupInvite,
    GroupMember,
    Match,
    MatchComment,
    Notification,
    NotificationSetting,
    User,
)


def get_user_by_email_or_username(db: Session, *, email: Optional[str] = None, username: Optional[str] = None) -> Optional[User]:
    user = None
    if email:
        user = db.query(User).filter(User.email == email).first()
    if not user and username:
        user = db.query(User).filter(or_(User.name == username, User.email == username)).first()
    return user


def get_announcement(db: Session, *, group_id: str, announcement_id: int) -> Optional[GroupAnnouncement]:
    return db.query(GroupAnnouncement).filter(GroupAnnouncement.id == announcement_id, GroupAnnouncement.group_id == group_id).first()


def list_announcements(db: Session, *, group_id: str) -> list[GroupAnnouncement]:
    return (
        db.query(GroupAnnouncement)
        .filter(GroupAnnouncement.group_id == group_id)
        .order_by(GroupAnnouncement.is_pinned.desc(), GroupAnnouncement.published_at.desc(), GroupAnnouncement.id.desc())
        .all()
    )


def get_match_in_group(db: Session, *, group_id: str, match_id: int) -> Optional[Match]:
    return db.query(Match).filter(Match.id == match_id, Match.group_id == group_id).first()


def get_comment(db: Session, *, group_id: str, match_id: int, comment_id: int) -> Optional[MatchComment]:
    return db.query(MatchComment).filter(MatchComment.id == comment_id, MatchComment.group_id == group_id, MatchComment.match_id == match_id).first()


def list_comments(db: Session, *, group_id: str, match_id: int) -> list[MatchComment]:
    return (
        db.query(MatchComment)
        .filter(MatchComment.group_id == group_id, MatchComment.match_id == match_id)
        .order_by(MatchComment.created_at.asc(), MatchComment.id.asc())
        .all()
    )


def list_notifications(db: Session, *, user_id: int, unread_only: bool, limit: int) -> list[Notification]:
    q = db.query(Notification).filter(Notification.user_id == user_id)
    if unread_only:
        q = q.filter(Notification.read.is_(False))
    return q.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all()


def get_notification(db: Session, *, notification_id: int, user_id: int) -> Optional[Notification]:
    return db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user_id).first()


def mark_all_notifications_read(db: Session, *, user_id: int) -> None:
    db.query(Notification).filter(Notification.user_id == user_id, Notification.read.is_(False)).update({Notification.read: True}, synchronize_session=False)


def get_notification_settings(db: Session, *, user_id: int) -> Optional[NotificationSetting]:
    return db.query(NotificationSetting).filter(NotificationSetting.user_id == user_id).first()


def get_invite(db: Session, *, invite_id: int, invited_user_id: Optional[int] = None) -> Optional[GroupInvite]:
    q = db.query(GroupInvite).filter(GroupInvite.id == invite_id)
    if invited_user_id is not None:
        q = q.filter(GroupInvite.invited_user_id == invited_user_id)
    return q.first()


def find_pending_group_invite(db: Session, *, group_id: str, invited_user_id: int) -> Optional[GroupInvite]:
    return db.query(GroupInvite).filter(GroupInvite.group_id == group_id, GroupInvite.invited_user_id == invited_user_id, GroupInvite.status == 'pending').first()


def list_group_invites(db: Session, *, group_id: str, status: Optional[str] = None) -> list[GroupInvite]:
    q = db.query(GroupInvite).filter(GroupInvite.group_id == group_id)
    if status:
        q = q.filter(GroupInvite.status == status)
    return q.order_by(GroupInvite.created_at.desc(), GroupInvite.id.desc()).all()


def get_group_membership(db: Session, *, group_id: str, user_id: int) -> Optional[GroupMember]:
    return db.query(GroupMember).filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id).first()


def list_group_activity(db: Session, *, group_id: str, limit: int) -> list[GroupActivityLog]:
    return (
        db.query(GroupActivityLog)
        .filter(GroupActivityLog.group_id == group_id)
        .order_by(GroupActivityLog.created_at.desc(), GroupActivityLog.id.desc())
        .limit(limit)
        .all()
    )
