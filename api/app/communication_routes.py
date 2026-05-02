from __future__ import annotations

from datetime import datetime
from typing import Optional
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import GroupAnnouncement, MatchComment, User, Group
from app.permissions import get_group_member, require_group_admin_or_owner
from app.security import get_current_user
from app.repositories.communication import get_invite
from app.services.communication_service import (
    accept_group_invite,
    create_group_announcement,
    create_group_comment,
    delete_group_announcement,
    delete_group_comment,
    get_group_activity_feed,
    get_group_announcements,
    get_group_comments,
    get_group_invites,
    get_notifications_for_user,
    get_unread_notifications_count,
    invite_user_to_group,
    mark_notification_read,
    mark_notifications_read,
    reject_group_invite,
    update_group_announcement,
    update_user_notification_settings,
    get_user_notification_settings,
    save_push_token,
    user_display_name,
)

router = APIRouter(tags=["communication"])


def _norm(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _user_display_name(user: Optional[User]) -> str:
    if not user:
        return "Usuário"
    return (user.name or user.email or f"Usuário {user.id}").strip()

def _looks_like_email(value: str) -> bool:
    value = (value or '').strip()
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', value))


class AnnouncementIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., min_length=1, max_length=140)
    message: str = Field(..., min_length=1)
    is_pinned: bool = Field(default=False, validation_alias=AliasChoices("is_pinned", "isPinned", "pinned"))

    @model_validator(mode="after")
    def _clean(self):
        self.title = self.title.strip()
        self.message = self.message.strip()
        return self


class AuthorOut(BaseModel):
    id: Optional[int] = None
    name: str


class AnnouncementOut(BaseModel):
    id: int
    group_id: str
    title: str
    message: str
    is_pinned: bool
    published_at: datetime
    author: AuthorOut


class AnnouncementUpdateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., min_length=1, max_length=140)
    message: str = Field(..., min_length=1)
    is_pinned: bool = Field(default=False, validation_alias=AliasChoices("is_pinned", "isPinned", "pinned"))

    @model_validator(mode="after")
    def _clean(self):
        self.title = self.title.strip()
        self.message = self.message.strip()
        return self


class CommentIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _clean(self):
        self.message = self.message.strip()
        return self


class CommentOut(BaseModel):
    id: int
    match_id: int
    group_id: str
    message: str
    created_at: datetime
    author: AuthorOut
    can_delete: bool


class NotificationOut(BaseModel):
    id: int
    type: str
    title: str
    message: str
    read: bool
    created_at: datetime
    payload: Optional[dict] = None


class NotificationCountOut(BaseModel):
    unread_count: int


class InviteIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    email: Optional[str] = None
    username: Optional[str] = None

    @model_validator(mode="after")
    def _check(self):
        self.email = (self.email or "").strip() or None
        self.username = (self.username or "").strip() or None
        if not self.email and not self.username:
            raise ValueError("Informe email ou username")
        if self.email and not _looks_like_email(self.email):
            raise ValueError("Email inválido")
        return self


class InviteOut(BaseModel):
    id: int
    group_id: str
    invited_user_id: int
    invited_user_name: str
    invited_by_user_id: Optional[int] = None
    invited_by_name: Optional[str] = None
    group_name: Optional[str] = None
    email: Optional[str] = None
    username: Optional[str] = None
    status: str
    created_at: datetime
    responded_at: Optional[datetime] = None


class ActivityOut(BaseModel):
    id: int
    activity_type: str
    title: str
    description: str
    created_at: datetime
    match_id: Optional[int] = None
    actor_user_id: Optional[int] = None


class NotificationSettingsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    matches_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("matches_enabled", "matches"))
    finance_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("finance_enabled", "finance"))
    announcements_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("announcements_enabled", "announcements"))
    comments_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("comments_enabled", "comments"))
    invites_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("invites_enabled", "invites"))
    fines_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("fines_enabled", "fines"))
    push_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("push_enabled", "push"))
    push_matches_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("push_matches_enabled", "push_matches"))
    push_finance_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("push_finance_enabled", "push_finance"))
    push_announcements_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("push_announcements_enabled", "push_announcements"))
    push_comments_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("push_comments_enabled", "push_comments"))
    push_invites_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("push_invites_enabled", "push_invites"))
    push_fines_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("push_fines_enabled", "push_fines"))


class NotificationSettingsOut(BaseModel):
    matches_enabled: bool
    finance_enabled: bool
    announcements_enabled: bool
    comments_enabled: bool
    invites_enabled: bool
    fines_enabled: bool
    push_enabled: bool
    push_matches_enabled: bool
    push_finance_enabled: bool
    push_announcements_enabled: bool
    push_comments_enabled: bool
    push_invites_enabled: bool
    push_fines_enabled: bool


class PushTokenIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    token: Optional[str] = None
    platform: Optional[str] = None


def _announcement_out(item: GroupAnnouncement) -> AnnouncementOut:
    return AnnouncementOut(
        id=item.id,
        group_id=item.group_id,
        title=item.title,
        message=item.message,
        is_pinned=item.is_pinned,
        published_at=item.published_at,
        author=AuthorOut(id=item.author.id if item.author else item.author_user_id, name=_user_display_name(item.author)),
    )


def _comment_out(item: MatchComment, current_user_id: int, is_admin: bool) -> CommentOut:
    return CommentOut(
        id=item.id,
        match_id=item.match_id,
        group_id=item.group_id,
        message=item.message,
        created_at=item.created_at,
        author=AuthorOut(id=item.user.id if item.user else item.user_id, name=_user_display_name(item.user)),
        can_delete=is_admin or item.user_id == current_user_id,
    )

def _invite_out(db: Session, item) -> InviteOut:
    invited_user = db.query(User).filter(User.id == item.invited_user_id).first()
    inviter = db.query(User).filter(User.id == item.invited_by_user_id).first() if item.invited_by_user_id else None
    group = db.query(Group).filter(Group.id == item.group_id).first()
    return InviteOut(
        id=item.id,
        group_id=item.group_id,
        invited_user_id=item.invited_user_id,
        invited_user_name=user_display_name(invited_user),
        invited_by_user_id=item.invited_by_user_id,
        invited_by_name=user_display_name(inviter) if inviter else None,
        group_name=group.name if group else None,
        email=item.email,
        username=item.username,
        status=item.status,
        created_at=item.created_at,
        responded_at=item.responded_at,
    )


@router.post("/groups/{group_id}/announcements", response_model=AnnouncementOut, status_code=201)
def create_announcement(group_id: str, payload: AnnouncementIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    require_group_admin_or_owner(db, group_id, current_user_id)
    item = create_group_announcement(db, group_id=group_id, current_user_id=current_user_id, title=payload.title, message=payload.message, is_pinned=payload.is_pinned)
    return _announcement_out(item)


@router.get("/groups/{group_id}/announcements", response_model=list[AnnouncementOut])
def list_announcements(group_id: str, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    get_group_member(db, group_id, current_user_id)
    return [_announcement_out(i) for i in get_group_announcements(db, group_id=group_id)]


@router.put("/groups/{group_id}/announcements/{announcement_id}", response_model=AnnouncementOut)
def update_announcement(group_id: str, announcement_id: int, payload: AnnouncementUpdateIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    require_group_admin_or_owner(db, group_id, current_user_id)
    item = update_group_announcement(db, group_id=group_id, announcement_id=announcement_id, title=payload.title, message=payload.message, is_pinned=payload.is_pinned)
    return _announcement_out(item)


@router.delete("/groups/{group_id}/announcements/{announcement_id}")
def delete_announcement(group_id: str, announcement_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    require_group_admin_or_owner(db, group_id, current_user_id)
    delete_group_announcement(db, group_id=group_id, announcement_id=announcement_id)
    return {"ok": True}


@router.get("/groups/{group_id}/matches/{match_id}/comments", response_model=list[CommentOut])
def list_comments(group_id: str, match_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _, member = get_group_member(db, group_id, current_user_id)
    items = get_group_comments(db, group_id=group_id, match_id=match_id)
    is_admin = _norm(member.role) in {"owner", "admin"}
    return [_comment_out(i, current_user_id, is_admin) for i in items]


@router.post("/groups/{group_id}/matches/{match_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(group_id: str, match_id: int, payload: CommentIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _, member = get_group_member(db, group_id, current_user_id)
    item = create_group_comment(db, group_id=group_id, match_id=match_id, current_user_id=current_user_id, message=payload.message)
    is_admin = _norm(member.role) in {"owner", "admin"}
    return _comment_out(item, current_user_id, is_admin)


@router.delete("/groups/{group_id}/matches/{match_id}/comments/{comment_id}")
def delete_comment(group_id: str, match_id: int, comment_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    _, member = get_group_member(db, group_id, current_user_id)
    item = db.query(MatchComment).filter(MatchComment.id == comment_id, MatchComment.group_id == group_id, MatchComment.match_id == match_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Comentário não encontrado")
    is_admin = _norm(member.role) in {"owner", "admin"}
    if not is_admin and item.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Sem permissão")
    delete_group_comment(db, group_id=group_id, match_id=match_id, comment_id=comment_id)
    return {"ok": True}


@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(limit: int = Query(default=50, ge=1, le=100), unread_only: bool = False, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    items = get_notifications_for_user(db, user_id=current_user_id, unread_only=unread_only, limit=limit)
    return [NotificationOut(id=i.id, type=i.type, title=i.title, message=i.message, read=i.read, created_at=i.created_at, payload=i.payload) for i in items]


@router.get("/notifications/unread-count", response_model=NotificationCountOut)
def notifications_unread_count(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    return NotificationCountOut(unread_count=get_unread_notifications_count(db, user_id=current_user_id))


@router.post("/notifications/read-all")
def read_all_notifications(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    mark_notifications_read(db, user_id=current_user_id)
    return {"ok": True}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    mark_notification_read(db, notification_id=notification_id, user_id=current_user_id)
    return {"ok": True}


@router.post("/groups/{group_id}/invite", response_model=InviteOut, status_code=201)
def invite_to_group(group_id: str, payload: InviteIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    group, _ = require_group_admin_or_owner(db, group_id, current_user_id)
    item, user = invite_user_to_group(db, group_id=group_id, current_user_id=current_user_id, email=payload.email, username=payload.username, group_name=group.name)
    return _invite_out(db, item)


@router.get("/groups/{group_id}/invites", response_model=list[InviteOut])
def list_group_invites(group_id: str, status: Optional[str] = None, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    require_group_admin_or_owner(db, group_id, current_user_id)
    items = get_group_invites(db, group_id=group_id, status=status)
    return [_invite_out(db, i) for i in items]


@router.get("/groups/invites/{invite_id}", response_model=InviteOut)
def get_invite_details(invite_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    item = get_invite(db, invite_id=invite_id, invited_user_id=current_user_id)
    if not item:
        raise HTTPException(status_code=404, detail="Convite não encontrado")
    return _invite_out(db, item)

@router.post("/groups/invites/{invite_id}/accept")
def accept_invite(invite_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    item = accept_group_invite(db, invite_id=invite_id, current_user_id=current_user_id)
    return {"ok": True, "group_id": item.group_id}


@router.post("/groups/invites/{invite_id}/reject")
def reject_invite(invite_id: int, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    reject_group_invite(db, invite_id=invite_id, current_user_id=current_user_id)
    return {"ok": True}


@router.get("/groups/{group_id}/activity", response_model=list[ActivityOut])
def list_group_activity(group_id: str, limit: int = Query(default=50, ge=1, le=100), db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    get_group_member(db, group_id, current_user_id)
    items = get_group_activity_feed(db, group_id=group_id, limit=limit)
    return [ActivityOut(id=i.id, activity_type=i.activity_type, title=i.title, description=i.description, created_at=i.created_at, match_id=i.match_id, actor_user_id=i.actor_user_id) for i in items]


@router.get("/users/me/notification-settings", response_model=NotificationSettingsOut)
def get_notification_settings(db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    settings = get_user_notification_settings(db, user_id=current_user_id)
    return NotificationSettingsOut(
        matches_enabled=settings.matches_enabled,
        finance_enabled=settings.finance_enabled,
        announcements_enabled=settings.announcements_enabled,
        comments_enabled=settings.comments_enabled,
        invites_enabled=settings.invites_enabled,
        fines_enabled=settings.fines_enabled,
        push_enabled=settings.push_enabled,
        push_matches_enabled=settings.push_matches_enabled,
        push_finance_enabled=settings.push_finance_enabled,
        push_announcements_enabled=settings.push_announcements_enabled,
        push_comments_enabled=settings.push_comments_enabled,
        push_invites_enabled=settings.push_invites_enabled,
        push_fines_enabled=settings.push_fines_enabled,
    )


@router.put("/users/me/notification-settings", response_model=NotificationSettingsOut)
def update_notification_settings(payload: NotificationSettingsIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    settings = update_user_notification_settings(db, user_id=current_user_id, data=payload.model_dump(exclude_unset=True, by_alias=False))
    return NotificationSettingsOut(
        matches_enabled=settings.matches_enabled,
        finance_enabled=settings.finance_enabled,
        announcements_enabled=settings.announcements_enabled,
        comments_enabled=settings.comments_enabled,
        invites_enabled=settings.invites_enabled,
        fines_enabled=settings.fines_enabled,
        push_enabled=settings.push_enabled,
        push_matches_enabled=settings.push_matches_enabled,
        push_finance_enabled=settings.push_finance_enabled,
        push_announcements_enabled=settings.push_announcements_enabled,
        push_comments_enabled=settings.push_comments_enabled,
        push_invites_enabled=settings.push_invites_enabled,
        push_fines_enabled=settings.push_fines_enabled,
    )


@router.put("/users/me/push-token")
def update_push_token(payload: PushTokenIn, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user)):
    save_push_token(db, user_id=current_user_id, token=payload.token, platform=payload.platform)
    return {"ok": True}
