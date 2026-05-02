from __future__ import annotations

from .common import *

# =====================================================
# FASE 10 - COMMUNICATION / NOTIFICATIONS
# =====================================================

class GroupAnnouncement(Base, TimestampMixin):
    __tablename__ = "group_announcements"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(140), nullable=False)
    message = Column(Text, nullable=False)
    is_pinned = Column(Boolean, nullable=False, default=False)
    published_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    group = relationship("Group")
    author = relationship("User")


class MatchComment(Base, TimestampMixin):
    __tablename__ = "match_comments"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    message = Column(Text, nullable=False)

    group = relationship("Group")
    match = relationship("Match")
    user = relationship("User")
    player = relationship("Player")


class NotificationSetting(Base, TimestampMixin):
    __tablename__ = "notification_settings"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    matches_enabled = Column(Boolean, nullable=False, default=True)
    finance_enabled = Column(Boolean, nullable=False, default=True)
    announcements_enabled = Column(Boolean, nullable=False, default=True)
    comments_enabled = Column(Boolean, nullable=False, default=True)
    invites_enabled = Column(Boolean, nullable=False, default=True)
    fines_enabled = Column(Boolean, nullable=False, default=True)
    push_enabled = Column(Boolean, nullable=False, default=True)
    push_matches_enabled = Column(Boolean, nullable=False, default=True)
    push_finance_enabled = Column(Boolean, nullable=False, default=True)
    push_announcements_enabled = Column(Boolean, nullable=False, default=True)
    push_comments_enabled = Column(Boolean, nullable=False, default=True)
    push_invites_enabled = Column(Boolean, nullable=False, default=True)
    push_fines_enabled = Column(Boolean, nullable=False, default=True)
    push_token = Column(String(512), nullable=True)
    push_platform = Column(String(30), nullable=True)
    push_token_updated_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(40), nullable=False, index=True)
    title = Column(String(160), nullable=False)
    message = Column(Text, nullable=False)
    read = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    external_key = Column(String(200), nullable=True, unique=True, index=True)
    payload = Column(JSONB, nullable=True)

    user = relationship("User")


class GroupInvite(Base, TimestampMixin):
    __tablename__ = "group_invites"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    invited_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    invited_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String(255), nullable=True, index=True)
    username = Column(String(120), nullable=True, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("group_id", "invited_user_id", "status", name="uq_group_invite_group_user_status"),
    )

    group = relationship("Group")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])
    invited_user = relationship("User", foreign_keys=[invited_user_id])


class GroupActivityLog(Base):
    __tablename__ = "group_activity_log"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    activity_type = Column(String(40), nullable=False, index=True)
    title = Column(String(160), nullable=False)
    description = Column(Text, nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    group = relationship("Group")
    actor_user = relationship("User", foreign_keys=[actor_user_id])
    actor_player = relationship("Player", foreign_keys=[actor_player_id])
    match = relationship("Match")
    target_user = relationship("User", foreign_keys=[target_user_id])
