"""phase11 friends feed reputation refine

Revision ID: 0052_phase11_friends_feed_reputation_refine
Revises: 0051_phase11_social_feed_ratings
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0052_phase11_friends_feed_reputation_refine"
down_revision = "0051_phase11_social_feed_ratings"
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
    matches_cols = {col["name"]: col for col in inspector.get_columns("matches")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    match_id_type = _clone_type(matches_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    if "social_feed_events" not in table_names:
        op.create_table(
            "social_feed_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("actor_player_id", player_id_type, nullable=True),
            sa.Column("target_player_id", player_id_type, nullable=True),
            sa.Column("group_id", group_id_type, nullable=True),
            sa.Column("match_id", match_id_type, nullable=True),
            sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["actor_player_id"], ["players.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["target_player_id"], ["players.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="SET NULL"),
        )

    inspector = inspect(bind)

    def ensure_index(table_name, index_name, cols, unique=False):
        existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if index_name not in existing:
            op.create_index(index_name, table_name, cols, unique=unique)

    ensure_index("social_feed_events", "ix_social_feed_events_event_type", ["event_type"])
    ensure_index("social_feed_events", "ix_social_feed_events_actor_player_id", ["actor_player_id"])
    ensure_index("social_feed_events", "ix_social_feed_events_target_player_id", ["target_player_id"])
    ensure_index("social_feed_events", "ix_social_feed_events_group_id", ["group_id"])
    ensure_index("social_feed_events", "ix_social_feed_events_match_id", ["match_id"])
    ensure_index("social_feed_events", "ix_social_feed_events_created_at", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "social_feed_events" not in table_names:
        return

    existing = {idx["name"] for idx in inspector.get_indexes("social_feed_events")}

    for index_name in [
        "ix_social_feed_events_created_at",
        "ix_social_feed_events_match_id",
        "ix_social_feed_events_group_id",
        "ix_social_feed_events_target_player_id",
        "ix_social_feed_events_actor_player_id",
        "ix_social_feed_events_event_type",
    ]:
        if index_name in existing:
            op.drop_index(index_name, table_name="social_feed_events")

    op.drop_table("social_feed_events")
