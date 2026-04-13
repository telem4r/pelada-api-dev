"""matches v2 stats and richer events

Revision ID: 0061_matches_v2_stats_and_richer_events
Revises: 0060_matches_v2_game_flow_events
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0061_matches_v2_stats_and_richer_events"
down_revision = "0060_matches_v2_game_flow_events"
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

    players_cols = {col["name"]: col for col in inspector.get_columns("players")}
    player_id_type = _clone_type(players_cols["id"]["type"])

    match_position_enum_v2 = postgresql.ENUM(
        "linha", "goleiro",
        name="match_position_enum_v2",
        create_type=False,
    )

    op.execute("ALTER TABLE public.match_events_v2 DROP CONSTRAINT IF EXISTS ck_match_events_v2_type")
    op.execute(
        "ALTER TABLE public.match_events_v2 "
        "ADD CONSTRAINT ck_match_events_v2_type "
        "CHECK (event_type in ('goal', 'assist', 'own_goal', 'yellow_card', 'red_card'))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_match_events_v2_match_id_event_type ON public.match_events_v2(match_id, event_type)")

    if "match_player_stats_v2" not in table_names:
        op.create_table(
            "match_player_stats_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("match_id", sa.Uuid(), nullable=False),
            sa.Column("team_number", sa.Integer(), nullable=False),
            sa.Column("entry_kind", sa.String(length=16), nullable=False),
            sa.Column("participant_id", sa.Uuid(), nullable=True),
            sa.Column("guest_id", sa.Uuid(), nullable=True),
            sa.Column("player_id", player_id_type, nullable=True),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("position", match_position_enum_v2, nullable=False),
            sa.Column("goals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("assists", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("own_goals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("yellow_cards", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("red_cards", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["match_id"], ["public.matches_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["participant_id"], ["public.match_participants_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["guest_id"], ["public.match_guests_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
            sa.CheckConstraint("entry_kind in ('member', 'guest')", name="ck_match_player_stats_v2_kind"),
            sa.CheckConstraint("team_number > 0", name="ck_match_player_stats_v2_team_number"),
            sa.CheckConstraint(
                """(
                    (participant_id is not null and guest_id is null)
                    or (participant_id is null and guest_id is not null)
                )""",
                name="ck_match_player_stats_v2_target",
            ),
            schema="public",
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_match_player_stats_v2_match_id ON public.match_player_stats_v2(match_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_match_player_stats_v2_player_id ON public.match_player_stats_v2(player_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_match_player_stats_v2_player_id")
    op.execute("DROP INDEX IF EXISTS ix_match_player_stats_v2_match_id")
    op.execute("DROP TABLE IF EXISTS public.match_player_stats_v2")
    op.execute("DROP INDEX IF EXISTS ix_match_events_v2_match_id_event_type")
    op.execute("ALTER TABLE public.match_events_v2 DROP CONSTRAINT IF EXISTS ck_match_events_v2_type")
    op.execute(
        "ALTER TABLE public.match_events_v2 "
        "ADD CONSTRAINT ck_match_events_v2_type "
        "CHECK (event_type in ('goal', 'yellow_card', 'red_card'))"
    )
