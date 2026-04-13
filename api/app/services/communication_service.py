from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.communication_utils import (
    create_group_activity,
    create_notification,
    dispatch_presence_confirmed,
    ensure_notification_settings,
    generate_due_match_reminders,
    notification_allowed,
    notify_group_members,
    register_push_token,
)
from app.core.time import utc_now
from app.models import GroupAnnouncement, GroupInvite, GroupMember, MatchComment, User
from app.permissions import get_user_primary_player
from app.repositories.communication import (
    find_pending_group_invite,
    get_announcement,
    get_comment,
    get_group_membership,
    get_invite,
    get_match_in_group,
    get_notification,
    get_user_by_email_or_username,
    list_announcements,
    list_comments,
    list_group_activity,
    list_group_invites,
    list_notifications,
    mark_all_notifications_read,
)


def user_display_name(user: Optional[User]) -> str:
    if not user:
        return 'Usuário'
    return (user.name or user.email or f'Usuário {user.id}').strip()


def create_group_announcement(db: Session, *, group_id: str, current_user_id: int, title: str, message: str, is_pinned: bool):
    item = GroupAnnouncement(group_id=group_id, author_user_id=current_user_id, title=title, message=message, is_pinned=is_pinned, published_at=utc_now())
    db.add(item)
    db.flush()
    author = db.query(User).filter(User.id == current_user_id).first()
    members = db.query(GroupMember).filter(GroupMember.group_id == group_id, GroupMember.status == 'active').all()
    for member in members:
        if member.user_id == current_user_id:
            continue
        if not notification_allowed(db, member.user_id, 'announcements'):
            continue
        create_notification(
            db,
            user_id=member.user_id,
            type='announcement',
            title='Novo aviso do grupo',
            message=title,
            external_key=f'announcement:{item.id}:{member.user_id}',
            payload={'group_id': group_id, 'announcement_id': item.id},
        )
    create_group_activity(db, group_id=group_id, activity_type='announcement_created', title='Novo aviso publicado', description=f'{user_display_name(author)} publicou: {title}', actor_user_id=current_user_id, metadata={'announcement_id': item.id})
    db.commit()
    db.refresh(item)
    return item


def update_group_announcement(db: Session, *, group_id: str, announcement_id: int, title: str, message: str, is_pinned: bool):
    item = get_announcement(db, group_id=group_id, announcement_id=announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail='Aviso não encontrado')
    item.title = title
    item.message = message
    item.is_pinned = is_pinned
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def delete_group_announcement(db: Session, *, group_id: str, announcement_id: int):
    item = get_announcement(db, group_id=group_id, announcement_id=announcement_id)
    if not item:
        raise HTTPException(status_code=404, detail='Aviso não encontrado')
    db.delete(item)
    db.commit()


def get_group_comments(db: Session, *, group_id: str, match_id: int):
    match = get_match_in_group(db, group_id=group_id, match_id=match_id)
    if not match:
        raise HTTPException(status_code=404, detail='Partida não encontrada')
    return list_comments(db, group_id=group_id, match_id=match_id)


def create_group_comment(db: Session, *, group_id: str, match_id: int, current_user_id: int, message: str):
    match = get_match_in_group(db, group_id=group_id, match_id=match_id)
    if not match:
        raise HTTPException(status_code=404, detail='Partida não encontrada')
    player = get_user_primary_player(db, current_user_id)
    item = MatchComment(group_id=group_id, match_id=match_id, user_id=current_user_id, player_id=player.id, message=message)
    db.add(item)
    db.flush()
    author = db.query(User).filter(User.id == current_user_id).first()
    notify_group_members(db, group_id=group_id, type='match_comment', title='Novo comentário na partida', message=f'{user_display_name(author)} comentou na partida.', category='comments', exclude_user_ids={current_user_id}, payload={'group_id': group_id, 'match_id': match_id, 'comment_id': item.id})
    create_group_activity(db, group_id=group_id, activity_type='match_comment', title='Novo comentário', description=f'{user_display_name(author)} comentou na partida.', actor_user_id=current_user_id, actor_player_id=player.id, match_id=match_id, metadata={'comment_id': item.id})
    db.commit()
    db.refresh(item)
    return item


def delete_group_comment(db: Session, *, group_id: str, match_id: int, comment_id: int):
    item = get_comment(db, group_id=group_id, match_id=match_id, comment_id=comment_id)
    if not item:
        raise HTTPException(status_code=404, detail='Comentário não encontrado')
    db.delete(item)
    db.commit()


def get_notifications_for_user(db: Session, *, user_id: int, unread_only: bool, limit: int):
    generate_due_match_reminders(db, user_id)
    return list_notifications(db, user_id=user_id, unread_only=unread_only, limit=limit)


def mark_notifications_read(db: Session, *, user_id: int):
    mark_all_notifications_read(db, user_id=user_id)
    db.commit()


def mark_notification_read(db: Session, *, notification_id: int, user_id: int):
    item = get_notification(db, notification_id=notification_id, user_id=user_id)
    if not item:
        raise HTTPException(status_code=404, detail='Notificação não encontrada')
    item.read = True
    db.add(item)
    db.commit()


def invite_user_to_group(db: Session, *, group_id: str, current_user_id: int, email: Optional[str], username: Optional[str], group_name: str):
    user = get_user_by_email_or_username(db, email=email, username=username)
    if not user:
        raise HTTPException(status_code=404, detail='Usuário não encontrado')
    membership = get_group_membership(db, group_id=group_id, user_id=user.id)
    if membership and (membership.status or '').lower() == 'active':
        raise HTTPException(status_code=400, detail='Usuário já é membro do grupo')
    existing = find_pending_group_invite(db, group_id=group_id, invited_user_id=user.id)
    if existing:
        return existing, user
    item = GroupInvite(group_id=group_id, invited_by_user_id=current_user_id, invited_user_id=user.id, email=user.email, username=user.name, status='pending')
    db.add(item)
    db.flush()
    if notification_allowed(db, user.id, 'invites'):
        create_notification(db, user_id=user.id, type='group_invite', title='Convite para grupo', message=f'Você foi convidado para o grupo {group_name}', external_key=f'group_invite:{item.id}:{user.id}', payload={'group_id': group_id, 'invite_id': item.id, 'group_name': group_name, 'invited_by_user_id': current_user_id, 'invited_by_name': user_display_name(db.query(User).filter(User.id == current_user_id).first())})
    create_group_activity(db, group_id=group_id, activity_type='group_invite', title='Convite enviado', description=f'Convite enviado para {user_display_name(user)}.', actor_user_id=current_user_id, target_user_id=user.id, metadata={'invite_id': item.id})
    db.commit()
    db.refresh(item)
    return item, user


def accept_group_invite(db: Session, *, invite_id: int, current_user_id: int):
    item = get_invite(db, invite_id=invite_id, invited_user_id=current_user_id)
    if not item:
        raise HTTPException(status_code=404, detail='Convite não encontrado')

    if item.status == 'accepted':
        membership = get_group_membership(db, group_id=item.group_id, user_id=current_user_id)
        if membership:
            membership.status = 'active'
            db.add(membership)
            db.commit()
        return item

    if item.status != 'pending':
        raise HTTPException(status_code=400, detail='Convite já respondido')

    player = get_user_primary_player(db, current_user_id)
    membership = get_group_membership(db, group_id=item.group_id, user_id=current_user_id)
    if membership:
        membership.status = 'active'
        membership.player_id = player.id
    else:
        membership = GroupMember(group_id=item.group_id, user_id=current_user_id, player_id=player.id, role='member', status='active')
        db.add(membership)

    existing_accepted = (
        db.query(GroupInvite)
        .filter(
            GroupInvite.group_id == item.group_id,
            GroupInvite.invited_user_id == current_user_id,
            GroupInvite.status == 'accepted',
            GroupInvite.id != item.id,
        )
        .order_by(GroupInvite.id.desc())
        .first()
    )
    if existing_accepted:
        existing_accepted.status = 'accepted_history'
        existing_accepted.responded_at = existing_accepted.responded_at or utc_now()
        db.add(existing_accepted)

    item.status = 'accepted'
    item.responded_at = utc_now()
    from app.models import Group
    group = db.query(Group).filter(Group.id == item.group_id).first()
    actor = db.query(User).filter(User.id == current_user_id).first()
    create_group_activity(db, group_id=item.group_id, activity_type='member_joined', title='Novo membro', description=f'{user_display_name(actor)} entrou no grupo.', actor_user_id=current_user_id, actor_player_id=player.id, metadata={'invite_id': item.id})
    if item.invited_by_user_id and notification_allowed(db, item.invited_by_user_id, 'invites'):
        create_notification(db, user_id=item.invited_by_user_id, type='invite_accepted', title='Convite aceito', message=f'{user_display_name(actor)} aceitou o convite para {group.name if group else "o grupo"}.', external_key=f'invite_accepted:{item.id}:{item.invited_by_user_id}', payload={'group_id': item.group_id, 'invite_id': item.id})

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # Cenário histórico: já existe um convite aceito para este utilizador/grupo.
        existing_accepted = (
            db.query(GroupInvite)
            .filter(
                GroupInvite.group_id == item.group_id,
                GroupInvite.invited_user_id == current_user_id,
                GroupInvite.status == 'accepted',
                GroupInvite.id != item.id,
            )
            .order_by(GroupInvite.id.desc())
            .first()
        )
        if existing_accepted:
            if membership:
                membership.status = 'active'
                membership.player_id = player.id
                db.add(membership)
                db.commit()
            return existing_accepted
        raise

    return item


def reject_group_invite(db: Session, *, invite_id: int, current_user_id: int):
    item = get_invite(db, invite_id=invite_id, invited_user_id=current_user_id)
    if not item:
        raise HTTPException(status_code=404, detail='Convite não encontrado')
    if item.status != 'pending':
        raise HTTPException(status_code=400, detail='Convite já respondido')
    item.status = 'rejected'
    item.responded_at = utc_now()
    db.add(item)
    db.commit()
    return item


def get_group_activity_feed(db: Session, *, group_id: str, limit: int):
    return list_group_activity(db, group_id=group_id, limit=limit)


def update_user_notification_settings(db: Session, *, user_id: int, data: dict):
    settings = ensure_notification_settings(db, user_id)
    allowed_keys = {
        'matches_enabled', 'finance_enabled', 'announcements_enabled', 'comments_enabled', 'invites_enabled', 'fines_enabled',
        'push_enabled', 'push_matches_enabled', 'push_finance_enabled', 'push_announcements_enabled', 'push_comments_enabled', 'push_invites_enabled', 'push_fines_enabled',
    }
    for key, value in data.items():
        if key in allowed_keys and value is not None:
            setattr(settings, key, bool(value))
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def get_user_notification_settings(db: Session, *, user_id: int):
    return ensure_notification_settings(db, user_id)


def save_push_token(db: Session, *, user_id: int, token: str | None, platform: str | None = None):
    return register_push_token(db, user_id=user_id, token=token, platform=platform)


def get_group_invites(db: Session, *, group_id: str, status: Optional[str]):
    return list_group_invites(db, group_id=group_id, status=status)


def get_group_announcements(db: Session, *, group_id: str):
    return list_announcements(db, group_id=group_id)



def get_unread_notifications_count(db: Session, *, user_id: int) -> int:
    generate_due_match_reminders(db, user_id)
    from app.models import Notification
    return int(
        db.query(Notification)
        .filter(Notification.user_id == user_id, Notification.read.is_(False))
        .count()
    )
