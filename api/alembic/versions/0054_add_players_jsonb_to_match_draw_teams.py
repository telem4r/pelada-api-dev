"""add players jsonb to match_draw_teams if missing

Revision ID: 0054_add_players_jsonb_to_match_draw_teams
Revises: 0053_add_push_notification_settings
Create Date: 2026-03-19

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0054_add_players_jsonb_to_match_draw_teams"
down_revision = "0053_add_push_notification_settings"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return False
    return any((col.get("name") or "") == column_name for col in columns)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("match_draw_teams"):
        return
    if not _has_column(inspector, "match_draw_teams", "players"):
        op.add_column(
            "match_draw_teams",
            sa.Column(
                "players",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
        )
        op.execute("UPDATE match_draw_teams SET players = '[]'::jsonb WHERE players IS NULL")
        op.alter_column("match_draw_teams", "players", server_default=None)


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("match_draw_teams") and _has_column(inspector, "match_draw_teams", "players"):
        op.drop_column("match_draw_teams", "players")
