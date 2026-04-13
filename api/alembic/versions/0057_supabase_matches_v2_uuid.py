"""supabase matches v2 uuid

Revision ID: 0057_supabase_matches_v2_uuid
Revises: 0056_add_match_modality_and_gender_type
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


revision = "0057_supabase_matches_v2_uuid"
down_revision = "0056_add_match_modality_and_gender_type"
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


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names(schema="public")) | set(inspector.get_table_names())

    groups_cols = {col["name"]: col for col in inspector.get_columns("groups")}
    users_cols = {col["name"]: col for col in inspector.get_columns("users")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    user_id_type = _clone_type(users_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_presence_status_enum_v2') THEN
                CREATE TYPE match_presence_status_enum_v2 AS ENUM ('confirmado', 'espera');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_position_enum_v2') THEN
                CREATE TYPE match_position_enum_v2 AS ENUM ('linha', 'goleiro');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_status_enum_v2') THEN
                CREATE TYPE match_status_enum_v2 AS ENUM ('scheduled', 'in_progress', 'finished', 'cancelled');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_draw_status_enum_v2') THEN
                CREATE TYPE match_draw_status_enum_v2 AS ENUM ('pending', 'generated');
            END IF;
        END$$;
        """
    )

    match_presence_status_enum_v2 = postgresql.ENUM(
        "confirmado", "espera",
        name="match_presence_status_enum_v2",
        create_type=False,
    )
    match_position_enum_v2 = postgresql.ENUM(
        "linha", "goleiro",
        name="match_position_enum_v2",
        create_type=False,
    )
    match_status_enum_v2 = postgresql.ENUM(
        "scheduled", "in_progress", "finished", "cancelled",
        name="match_status_enum_v2",
        create_type=False,
    )
    match_draw_status_enum_v2 = postgresql.ENUM(
        "pending", "generated",
        name="match_draw_status_enum_v2",
        create_type=False,
    )

    if "matches_v2" not in table_names:
        op.create_table(
            "matches_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("created_by_user_id", user_id_type, nullable=False),
            sa.Column("title", sa.String(length=120), nullable=True),
            sa.Column("status", match_status_enum_v2, nullable=False, server_default="scheduled"),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("location_name", sa.String(length=160), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("line_slots", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("goalkeeper_slots", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("draw_status", match_draw_status_enum_v2, nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
            sa.CheckConstraint("(line_slots + goalkeeper_slots) > 0", name="ck_matches_v2_slots_positive"),
            sa.CheckConstraint("ends_at > starts_at", name="ck_matches_v2_ends_after_start"),
            schema="public",
        )

    if "match_participants_v2" not in table_names:
        op.create_table(
            "match_participants_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("match_id", sa.Uuid(), nullable=False),
            sa.Column("player_id", player_id_type, nullable=False),
            sa.Column("user_id", user_id_type, nullable=False),
            sa.Column("position", match_position_enum_v2, nullable=False),
            sa.Column("status", match_presence_status_enum_v2, nullable=False),
            sa.Column("queue_order", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("has_arrived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["match_id"], ["public.matches_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("match_id", "player_id", name="uq_match_participants_v2_match_player"),
            schema="public",
        )

    if "match_guests_v2" not in table_names:
        op.create_table(
            "match_guests_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("match_id", sa.Uuid(), nullable=False),
            sa.Column("created_by_user_id", user_id_type, nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("position", match_position_enum_v2, nullable=False),
            sa.Column("status", match_presence_status_enum_v2, nullable=False),
            sa.Column("queue_order", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("has_arrived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["match_id"], ["public.matches_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
            schema="public",
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_matches_v2_group_id_starts_at ON public.matches_v2(group_id, starts_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_match_participants_v2_match_id_status ON public.match_participants_v2(match_id, status, position, queue_order)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_match_guests_v2_match_id_status ON public.match_guests_v2(match_id, status, position, queue_order)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_match_guests_v2_match_id_status")
    op.execute("DROP INDEX IF EXISTS ix_match_participants_v2_match_id_status")
    op.execute("DROP INDEX IF EXISTS ix_matches_v2_group_id_starts_at")
    op.execute("DROP TABLE IF EXISTS public.match_guests_v2")
    op.execute("DROP TABLE IF EXISTS public.match_participants_v2")
    op.execute("DROP TABLE IF EXISTS public.matches_v2")
    op.execute("DROP TYPE IF EXISTS match_draw_status_enum_v2")
    op.execute("DROP TYPE IF EXISTS match_status_enum_v2")
    op.execute("DROP TYPE IF EXISTS match_position_enum_v2")
    op.execute("DROP TYPE IF EXISTS match_presence_status_enum_v2")
