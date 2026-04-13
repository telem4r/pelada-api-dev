"""create domain tables (teams, players, matches)

Revision ID: 0002_domain_tables
Revises: 0001_create_users
Create Date: 2026-02-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_domain_tables"
down_revision = "0001_create_users"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_teams_id", "teams", ["id"])
    op.create_index("ix_teams_owner_id", "teams", ["owner_id"])

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_players_id", "players", ["id"])
    op.create_index("ix_players_owner_id", "players", ["owner_id"])
    op.create_index("ix_players_team_id", "players", ["team_id"])

    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("away_team_id", sa.Integer(), sa.ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_matches_id", "matches", ["id"])
    op.create_index("ix_matches_owner_id", "matches", ["owner_id"])
    op.create_index("ix_matches_home_team_id", "matches", ["home_team_id"])
    op.create_index("ix_matches_away_team_id", "matches", ["away_team_id"])


def downgrade():
    op.drop_index("ix_matches_away_team_id", table_name="matches")
    op.drop_index("ix_matches_home_team_id", table_name="matches")
    op.drop_index("ix_matches_owner_id", table_name="matches")
    op.drop_index("ix_matches_id", table_name="matches")
    op.drop_table("matches")

    op.drop_index("ix_players_team_id", table_name="players")
    op.drop_index("ix_players_owner_id", table_name="players")
    op.drop_index("ix_players_id", table_name="players")
    op.drop_table("players")

    op.drop_index("ix_teams_owner_id", table_name="teams")
    op.drop_index("ix_teams_id", table_name="teams")
    op.drop_table("teams")
