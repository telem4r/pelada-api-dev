"""0040 add match ends_at

Revision ID: 0040_add_match_ends_at
Revises: 0039_add_match_events_game_flow
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_add_match_ends_at"
down_revision = "0039_add_match_events_game_flow"
branch_labels = None
depends_on = None

def _has_column(bind, table, column):
    return bind.execute(sa.text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"), {"t": table, "c": column}).fetchone() is not None

def upgrade():
    bind = op.get_bind()
    if not _has_column(bind, "matches", "ends_at"):
        op.add_column("matches", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True))
    bind.execute(sa.text("UPDATE matches SET ends_at = COALESCE(ends_at, starts_at + interval '2 hour') WHERE starts_at IS NOT NULL"))

def downgrade():
    bind = op.get_bind()
    if _has_column(bind, "matches", "ends_at"):
        op.drop_column("matches", "ends_at")
