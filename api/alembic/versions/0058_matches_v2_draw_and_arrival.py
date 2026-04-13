"""matches v2 draw and arrival

Revision ID: 0058_matches_v2_draw_and_arrival
Revises: 0057_supabase_matches_v2_uuid
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0058_matches_v2_draw_and_arrival"
down_revision = "0057_supabase_matches_v2_uuid"
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

    if "match_draws_v2" not in table_names:
        op.create_table(
            "match_draws_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("match_id", sa.Uuid(), nullable=False, unique=True),
            sa.Column("generated_by_user_id", user_id_type, nullable=False),
            sa.Column("team_count", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["match_id"], ["public.matches_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["generated_by_user_id"], ["users.id"], ondelete="RESTRICT"),
            sa.CheckConstraint("team_count between 2 and 4", name="ck_match_draws_v2_team_count"),
            schema="public",
        )

    if "match_draw_entries_v2" not in table_names:
        op.create_table(
            "match_draw_entries_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("draw_id", sa.Uuid(), nullable=False),
            sa.Column("team_number", sa.Integer(), nullable=False),
            sa.Column("entry_kind", sa.String(length=16), nullable=False),
            sa.Column("participant_id", sa.Uuid(), nullable=True),
            sa.Column("guest_id", sa.Uuid(), nullable=True),
            sa.Column("player_id", player_id_type, nullable=True),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("position", match_position_enum_v2, nullable=False),
            sa.Column("skill_rating", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["draw_id"], ["public.match_draws_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["participant_id"], ["public.match_participants_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["guest_id"], ["public.match_guests_v2.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
            sa.CheckConstraint("entry_kind in ('member', 'guest')", name="ck_match_draw_entries_v2_kind"),
            sa.CheckConstraint("team_number > 0", name="ck_match_draw_entries_v2_team_number"),
            sa.CheckConstraint(
                """(
                    (participant_id is not null and guest_id is null)
                    or (participant_id is null and guest_id is not null)
                )""",
                name="ck_match_draw_entries_v2_target",
            ),
            schema="public",
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_match_draw_entries_v2_draw_id ON public.match_draw_entries_v2(draw_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_match_draw_entries_v2_team_number ON public.match_draw_entries_v2(team_number)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_match_draw_entries_v2_team_number")
    op.execute("DROP INDEX IF EXISTS ix_match_draw_entries_v2_draw_id")
    op.execute("DROP TABLE IF EXISTS public.match_draw_entries_v2")
    op.execute("DROP TABLE IF EXISTS public.match_draws_v2")
