"""social v2 follow feed

Revision ID: 0064_social_v2_follow_feed
Revises: 0063_notifications_v2_realtime_feed
Create Date: 2026-03-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0064_social_v2_follow_feed"
down_revision = "0063_notifications_v2_realtime_feed"
branch_labels = None
depends_on = None


def _clone_type(col_type):
    if isinstance(col_type, sa.Integer):
        return sa.Integer()
    if isinstance(col_type, sa.BigInteger):
        return sa.BigInteger()
    if isinstance(col_type, sa.String):
        return sa.String(length=col_type.length)
    if isinstance(col_type, sa.Uuid):
        return sa.Uuid()
    return col_type.__class__()


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names(schema="public")) | set(inspector.get_table_names())

    players_cols = {col["name"]: col for col in inspector.get_columns("players")}
    player_id_type = _clone_type(players_cols["id"]["type"])

    if "social_follows_v2" not in table_names:
        op.create_table(
            "social_follows_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("follower_player_id", player_id_type, nullable=False),
            sa.Column("followed_player_id", player_id_type, nullable=False),
            sa.Column("followed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["follower_player_id"], ["players.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["followed_player_id"], ["players.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("follower_player_id", "followed_player_id", name="uq_social_follows_v2"),
            sa.CheckConstraint("follower_player_id <> followed_player_id", name="ck_social_follows_v2_not_self"),
            schema="public",
        )

    op.execute("create index if not exists ix_social_follows_v2_follower on public.social_follows_v2(follower_player_id)")
    op.execute("create index if not exists ix_social_follows_v2_followed on public.social_follows_v2(followed_player_id)")


def downgrade():
    op.execute("drop index if exists ix_social_follows_v2_followed")
    op.execute("drop index if exists ix_social_follows_v2_follower")
    op.execute("drop table if exists public.social_follows_v2")
