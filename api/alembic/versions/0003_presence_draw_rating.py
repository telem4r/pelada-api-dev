"""presence + draw + player rating

Revision ID: 0003_presence_draw_rating
Revises: 0002_domain_tables
Create Date: 2026-02-18

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_presence_draw_rating"
down_revision = "0002_domain_tables"
branch_labels = None
depends_on = None


def upgrade():
    # players: add rating (0..5)
    op.add_column("players", sa.Column("rating", sa.Integer(), nullable=False, server_default="0"))
    op.alter_column("players", "rating", server_default=None)

    # match_participants
    op.create_table(
        "match_participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="confirmed"),
        sa.Column("arrived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("match_id", "player_id", name="uq_match_participant"),
    )
    op.create_index("ix_match_participants_id", "match_participants", ["id"])
    op.create_index("ix_match_participants_match_id", "match_participants", ["match_id"])
    op.create_index("ix_match_participants_player_id", "match_participants", ["player_id"])

    # match_draw_teams
    op.create_table(
        "match_draw_teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_number", sa.Integer(), nullable=False),
        sa.Column(
            "players",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("match_id", "team_number", name="uq_match_team_number"),
    )
    op.create_index("ix_match_draw_teams_id", "match_draw_teams", ["id"])
    op.create_index("ix_match_draw_teams_match_id", "match_draw_teams", ["match_id"])


def downgrade():
    op.drop_index("ix_match_draw_teams_match_id", table_name="match_draw_teams")
    op.drop_index("ix_match_draw_teams_id", table_name="match_draw_teams")
    op.drop_table("match_draw_teams")

    op.drop_index("ix_match_participants_player_id", table_name="match_participants")
    op.drop_index("ix_match_participants_match_id", table_name="match_participants")
    op.drop_index("ix_match_participants_id", table_name="match_participants")
    op.drop_table("match_participants")

    op.drop_column("players", "rating")
