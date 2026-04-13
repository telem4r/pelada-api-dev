"""phase10 communication and notifications

Revision ID: 0045_phase10_communication_and_notifications
Revises: 0044_phase9_player_achievements
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0045_phase10_communication_and_notifications"
down_revision = "0044_phase9_player_achievements"
branch_labels = None
depends_on = None


def _clone_type(col_type):
    if isinstance(col_type, sa.Integer):
        return sa.Integer()
    if isinstance(col_type, sa.BigInteger):
        return sa.BigInteger()
    if isinstance(col_type, sa.String):
        return sa.String(length=col_type.length)
    return col_type.__class__()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    table_names = set(inspector.get_table_names())

    groups_cols = {col["name"]: col for col in inspector.get_columns("groups")}
    users_cols = {col["name"]: col for col in inspector.get_columns("users")}
    matches_cols = {col["name"]: col for col in inspector.get_columns("matches")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    user_id_type = _clone_type(users_cols["id"]["type"])
    match_id_type = _clone_type(matches_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    if "group_announcements" not in table_names:
        op.create_table(
            "group_announcements",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("author_user_id", user_id_type, nullable=True),
            sa.Column("title", sa.String(length=140), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="SET NULL"),
        )

    if "match_comments" not in table_names:
        op.create_table(
            "match_comments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("match_id", match_id_type, nullable=False),
            sa.Column("user_id", user_id_type, nullable=False),
            sa.Column("player_id", player_id_type, nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
        )

    if "notification_settings" not in table_names:
        op.create_table(
            "notification_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", user_id_type, nullable=False),
            sa.Column("matches_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("finance_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("announcements_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("comments_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("invites_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("fines_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", name="uq_notification_settings_user_id"),
        )

    if "notifications" not in table_names:
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", user_id_type, nullable=False),
            sa.Column("type", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("external_key", sa.String(length=200), nullable=True),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("external_key", name="uq_notifications_external_key"),
        )

    if "group_invites" not in table_names:
        op.create_table(
            "group_invites",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("invited_by_user_id", user_id_type, nullable=True),
            sa.Column("invited_user_id", user_id_type, nullable=False),
            sa.Column("email", sa.String(length=255), nullable=True),
            sa.Column("username", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["invited_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("group_id", "invited_user_id", "status", name="uq_group_invite_group_user_status"),
        )

    if "group_activity_log" not in table_names:
        op.create_table(
            "group_activity_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("actor_user_id", user_id_type, nullable=True),
            sa.Column("actor_player_id", player_id_type, nullable=True),
            sa.Column("activity_type", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("match_id", match_id_type, nullable=True),
            sa.Column("target_user_id", user_id_type, nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["actor_player_id"], ["players.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="SET NULL"),
        )

    inspector = inspect(bind)

    def ensure_index(table_name, index_name, cols, unique=False):
        existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if index_name not in existing:
            op.create_index(index_name, table_name, cols, unique=unique)

    ensure_index("group_announcements", "ix_group_announcements_group_id", ["group_id"])
    ensure_index("group_announcements", "ix_group_announcements_author_user_id", ["author_user_id"])

    ensure_index("match_comments", "ix_match_comments_group_id", ["group_id"])
    ensure_index("match_comments", "ix_match_comments_match_id", ["match_id"])
    ensure_index("match_comments", "ix_match_comments_user_id", ["user_id"])
    ensure_index("match_comments", "ix_match_comments_player_id", ["player_id"])

    ensure_index("notification_settings", "ix_notification_settings_user_id", ["user_id"], unique=True)

    ensure_index("notifications", "ix_notifications_user_id", ["user_id"])
    ensure_index("notifications", "ix_notifications_type", ["type"])
    ensure_index("notifications", "ix_notifications_read", ["read"])
    ensure_index("notifications", "ix_notifications_external_key", ["external_key"], unique=True)

    ensure_index("group_invites", "ix_group_invites_group_id", ["group_id"])
    ensure_index("group_invites", "ix_group_invites_invited_by_user_id", ["invited_by_user_id"])
    ensure_index("group_invites", "ix_group_invites_invited_user_id", ["invited_user_id"])
    ensure_index("group_invites", "ix_group_invites_email", ["email"])
    ensure_index("group_invites", "ix_group_invites_username", ["username"])
    ensure_index("group_invites", "ix_group_invites_status", ["status"])

    ensure_index("group_activity_log", "ix_group_activity_log_group_id", ["group_id"])
    ensure_index("group_activity_log", "ix_group_activity_log_actor_user_id", ["actor_user_id"])
    ensure_index("group_activity_log", "ix_group_activity_log_actor_player_id", ["actor_player_id"])
    ensure_index("group_activity_log", "ix_group_activity_log_activity_type", ["activity_type"])
    ensure_index("group_activity_log", "ix_group_activity_log_match_id", ["match_id"])
    ensure_index("group_activity_log", "ix_group_activity_log_target_user_id", ["target_user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    def drop_index_if_exists(table_name, index_name):
        existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if index_name in existing:
            op.drop_index(index_name, table_name=table_name)

    if "group_activity_log" in table_names:
        drop_index_if_exists("group_activity_log", "ix_group_activity_log_target_user_id")
        drop_index_if_exists("group_activity_log", "ix_group_activity_log_match_id")
        drop_index_if_exists("group_activity_log", "ix_group_activity_log_activity_type")
        drop_index_if_exists("group_activity_log", "ix_group_activity_log_actor_player_id")
        drop_index_if_exists("group_activity_log", "ix_group_activity_log_actor_user_id")
        drop_index_if_exists("group_activity_log", "ix_group_activity_log_group_id")
        op.drop_table("group_activity_log")

    if "group_invites" in table_names:
        drop_index_if_exists("group_invites", "ix_group_invites_status")
        drop_index_if_exists("group_invites", "ix_group_invites_username")
        drop_index_if_exists("group_invites", "ix_group_invites_email")
        drop_index_if_exists("group_invites", "ix_group_invites_invited_user_id")
        drop_index_if_exists("group_invites", "ix_group_invites_invited_by_user_id")
        drop_index_if_exists("group_invites", "ix_group_invites_group_id")
        op.drop_table("group_invites")

    if "notifications" in table_names:
        drop_index_if_exists("notifications", "ix_notifications_external_key")
        drop_index_if_exists("notifications", "ix_notifications_read")
        drop_index_if_exists("notifications", "ix_notifications_type")
        drop_index_if_exists("notifications", "ix_notifications_user_id")
        op.drop_table("notifications")

    if "notification_settings" in table_names:
        drop_index_if_exists("notification_settings", "ix_notification_settings_user_id")
        op.drop_table("notification_settings")

    if "match_comments" in table_names:
        drop_index_if_exists("match_comments", "ix_match_comments_player_id")
        drop_index_if_exists("match_comments", "ix_match_comments_user_id")
        drop_index_if_exists("match_comments", "ix_match_comments_match_id")
        drop_index_if_exists("match_comments", "ix_match_comments_group_id")
        op.drop_table("match_comments")

    if "group_announcements" in table_names:
        drop_index_if_exists("group_announcements", "ix_group_announcements_author_user_id")
        drop_index_if_exists("group_announcements", "ix_group_announcements_group_id")
        op.drop_table("group_announcements")
