"""matches v2 game flow events

Revision ID: 0060_matches_v2_game_flow_events
Revises: 0059_matches_v2_operation_locks_and_draw_balance
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0060_matches_v2_game_flow_events"
down_revision = "0059_matches_v2_operation_locks_and_draw_balance"
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

    users_cols = {col["name"]: col for col in inspector.get_columns("users")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    user_id_type = _clone_type(users_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    match_position_enum_v2 = postgresql.ENUM(
        "linha", "goleiro",
        name="match_position_enum_v2",
        create_type=False,
    )

    op.execute("""
        ALTER TABLE public.matches_v2
        ADD COLUMN IF NOT EXISTS started_at timestamptz NULL
    """)
    op.execute("""
        ALTER TABLE public.matches_v2
        ADD COLUMN IF NOT EXISTS finished_at timestamptz NULL
    """)

    if "match_events_v2" not in table_names:
        op.create_table(
            "match_events_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("match_id", sa.Uuid(), nullable=False),
            sa.Column("created_by_user_id", user_id_type, nullable=False),
            sa.Column("team_number", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=24), nullable=False),
            sa.Column("participant_id", sa.Uuid(), nullable=True),
            sa.Column("guest_id", sa.Uuid(), nullable=True),
            sa.Column("player_id", player_id_type, nullable=True),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("position", match_position_enum_v2, nullable=False),
            sa.Column("minute", sa.Integer(), nullable=True),
            sa.Column("notes", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["match_id"], ["public.matches_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["participant_id"], ["public.match_participants_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["guest_id"], ["public.match_guests_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
            sa.CheckConstraint("team_number > 0", name="ck_match_events_v2_team_number"),
            sa.CheckConstraint("event_type in ('goal', 'yellow_card', 'red_card')", name="ck_match_events_v2_type"),
            sa.CheckConstraint(
                """(
                    (participant_id is not null and guest_id is null)
                    or (participant_id is null and guest_id is not null)
                )""",
                name="ck_match_events_v2_target",
            ),
            sa.CheckConstraint(
                "minute is null or minute between 0 and 200",
                name="ck_match_events_v2_minute",
            ),
            schema="public",
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_match_events_v2_match_id_created_at ON public.match_events_v2(match_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_match_events_v2_team_number ON public.match_events_v2(team_number)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_match_events_v2_team_number")
    op.execute("DROP INDEX IF EXISTS ix_match_events_v2_match_id_created_at")
    op.execute("DROP TABLE IF EXISTS public.match_events_v2")
    op.execute("ALTER TABLE public.matches_v2 DROP COLUMN IF EXISTS finished_at")
    op.execute("ALTER TABLE public.matches_v2 DROP COLUMN IF EXISTS started_at")
