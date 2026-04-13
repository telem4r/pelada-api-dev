"""0039 add match events game flow

Revision ID: 0039_add_match_events_game_flow
Revises: 0038_align_match_guests_presence_fields
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_add_match_events_game_flow"
down_revision = "0038_align_match_guests_presence_fields"
branch_labels = None
depends_on = None

def _has_table(bind, table):
    return bind.execute(sa.text("SELECT 1 FROM information_schema.tables WHERE table_name=:t"), {"t": table}).fetchone() is not None

def upgrade():
    bind = op.get_bind()
    if not _has_table(bind, "match_events"):
        op.create_table(
            "match_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", sa.String(length=36), nullable=True),
            sa.Column("match_id", sa.Integer(), nullable=False),
            sa.Column("team_number", sa.Integer(), nullable=False),
            sa.Column("player_id", sa.Integer(), nullable=True),
            sa.Column("guest_id", sa.Integer(), nullable=True),
            sa.Column("event_type", sa.String(length=30), nullable=False, server_default="goal"),
            sa.Column("minute", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_match_events_match_id", "match_events", ["match_id"])
        op.create_index("ix_match_events_group_id", "match_events", ["group_id"])

def downgrade():
    bind = op.get_bind()
    if _has_table(bind, "match_events"):
        op.drop_table("match_events")
