from __future__ import annotations

from datetime import timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import (
    Group,
    GroupActivityLog,
    GroupMember,
    Match,
    MatchParticipant,
    Notification,
    NotificationSetting,
    ParticipantStatus,
    User,
)


def _norm(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def ensure_notification_settings(db: Session, user_id: int) -> NotificationSetting:
    row = db.query(NotificationSetting).filter(NotificationSetting.user_id == user_id).first()
    if row:
        return row
    row = NotificationSetting(user_id=user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def notification_allowed(db: Session, user_id: int, category: str, *, push: bool = False) -> bool:
    s = ensure_notification_settings(db, user_id)
    mapping = {
        "matches": s.push_matches_enabled if push else s.matches_enabled,
        "finance": s.push_finance_enabled if push else s.finance_enabled,
        "announcements": s.push_announcements_enabled if push else s.announcements_enabled,
        "comments": s.push_comments_enabled if push else s.comments_enabled,
        "invites": s.push_invites_enabled if push else s.invites_enabled,
        "fines": s.push_fines_enabled if push else s.fines_enabled,
    }
    if push and not s.push_enabled:
        return False
    return bool(mapping.get(category, True))


def push_notification_allowed(db: Session, user_id: int, category: str) -> bool:
    return notification_allowed(db, user_id, category, push=True)


def register_push_token(db: Session, *, user_id: int, token: Optional[str], platform: Optional[str] = None) -> NotificationSetting:
    settings = ensure_notification_settings(db, user_id)
    settings.push_token = (token or "").strip() or None
    settings.push_platform = (platform or "").strip() or None
    settings.push_token_updated_at = utc_now() if settings.push_token else None
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def create_notification(
    db: Session,
    *,
    user_id: int,
    type: str,
    title: str,
    message: str,
    external_key: Optional[str] = None,
    payload: Optional[dict] = None,
) -> Optional[Notification]:
    if external_key:
        existing = db.query(Notification).filter(Notification.external_key == external_key).first()
        if existing:
            return existing
    item = Notification(
        user_id=user_id,
        type=type,
        title=title,
        message=message,
        external_key=external_key,
        payload=payload,
        read=False,
        created_at=utc_now(),
    )
    db.add(item)
    db.flush()
    return item


def create_group_activity(
    db: Session,
    *,
    group_id: str,
    activity_type: str,
    title: str,
    description: str,
    actor_user_id: Optional[int] = None,
    actor_player_id: Optional[int] = None,
    match_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> GroupActivityLog:
    item = GroupActivityLog(
        group_id=group_id,
        activity_type=activity_type,
        title=title,
        description=description,
        actor_user_id=actor_user_id,
        actor_player_id=actor_player_id,
        match_id=match_id,
        target_user_id=target_user_id,
        metadata_json=metadata,
        created_at=utc_now(),
    )
    db.add(item)
    db.flush()
    return item


def notify_group_members(
    db: Session,
    *,
    group_id: str,
    type: str,
    title: str,
    message: str,
    category: str,
    exclude_user_ids: Optional[Iterable[int]] = None,
    payload: Optional[dict] = None,
) -> int:
    exclude = set(exclude_user_ids or [])
    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.status == "active")
        .all()
    )
    count = 0
    for member in members:
        if member.user_id in exclude:
            continue
        if not notification_allowed(db, member.user_id, category):
            continue
        create_notification(
            db,
            user_id=member.user_id,
            type=type,
            title=title,
            message=message,
            payload=payload,
        )
        count += 1
    return count


def dispatch_match_created(db: Session, match: Match, actor_user_id: int) -> None:
    if not match.group_id:
        return
    group = db.query(Group).filter(Group.id == match.group_id).first()
    group_name = group.name if group else "grupo"
    when = match.starts_at.strftime("%d/%m %H:%M") if match.starts_at else "em breve"
    title = "Nova partida criada"
    message = f"Nova partida criada em {group_name} para {when}."
    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == match.group_id, GroupMember.status == "active")
        .all()
    )
    for member in members:
        if member.user_id == actor_user_id:
            continue
        if not notification_allowed(db, member.user_id, "matches"):
            continue
        create_notification(
            db,
            user_id=member.user_id,
            type="match_created",
            title=title,
            message=message,
            external_key=f"match_created:{match.id}:{member.user_id}",
            payload={"group_id": match.group_id, "match_id": match.id},
        )
    create_group_activity(
        db,
        group_id=match.group_id,
        activity_type="match_created",
        title=title,
        description=message,
        actor_user_id=actor_user_id,
        match_id=match.id,
        metadata={"group_id": match.group_id, "match_id": match.id},
    )


def dispatch_presence_confirmed(db: Session, match: Match, user: User, player_id: Optional[int] = None) -> None:
    if not match.group_id:
        return
    when = match.starts_at.strftime("%d/%m %H:%M") if match.starts_at else "em breve"
    create_group_activity(
        db,
        group_id=match.group_id,
        activity_type="presence_confirmed",
        title="Presença confirmada",
        description=f"{user.name} confirmou presença na partida de {when}.",
        actor_user_id=user.id,
        actor_player_id=player_id,
        match_id=match.id,
        metadata={"match_id": match.id},
    )
    if notification_allowed(db, user.id, "matches"):
        create_notification(
            db,
            user_id=user.id,
            type="presence_confirmed",
            title="Presença confirmada",
            message="Você confirmou presença na partida.",
            external_key=f"presence_confirmed:{match.id}:{user.id}",
            payload={"group_id": match.group_id, "match_id": match.id},
        )


def generate_due_match_reminders(db: Session, user_id: int) -> int:
    now = utc_now()
    created = 0
    parts = (
        db.query(MatchParticipant, Match)
        .join(Match, Match.id == MatchParticipant.match_id)
        .filter(MatchParticipant.status.in_([ParticipantStatus.confirmed.value, ParticipantStatus.waitlist.value]))
        .filter(Match.starts_at > now)
        .filter(Match.starts_at <= now + timedelta(hours=24, minutes=5))
        .all()
    )
    for part, match in parts:
        if not match.group_id:
            continue
        gm = db.query(GroupMember).filter(GroupMember.group_id == match.group_id, GroupMember.player_id == part.player_id).first()
        if not gm or gm.user_id != user_id:
            continue
        if not notification_allowed(db, user_id, "matches"):
            continue
        delta = match.starts_at - now
        minutes = int(delta.total_seconds() // 60)
        reminder_kind = None
        if 115 <= minutes <= 125:
            reminder_kind = "2h"
            text = "Sua partida começa em 2 horas"
        elif 25 <= minutes <= 35:
            reminder_kind = "30m"
            text = "Sua partida começa em 30 minutos"
        else:
            continue
        external_key = f"reminder:{match.id}:{user_id}:{reminder_kind}"
        n = create_notification(
            db,
            user_id=user_id,
            type="match_reminder",
            title="Lembrete de partida",
            message=text,
            external_key=external_key,
            payload={"group_id": match.group_id, "match_id": match.id, "reminder": reminder_kind},
        )
        if n:
            created += 1
    if created:
        db.commit()
    return created
