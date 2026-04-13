"""0041 phase7 stats mvp and history

Revision ID: 0041_phase7_stats_mvp_and_history
Revises: 0040_add_match_ends_at
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_phase7_stats_mvp_and_history"
down_revision = "0040_add_match_ends_at"
branch_labels = None
depends_on = None

def _has_column(bind, table, column):
    return bind.execute(sa.text("SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"), {"t": table, "c": column}).fetchone() is not None

def upgrade():
    bind = op.get_bind()
    if not _has_column(bind, "matches", "mvp_player_id"):
        op.add_column("matches", sa.Column("mvp_player_id", sa.Integer(), nullable=True))
    if not _has_column(bind, "matches", "mvp_guest_id"):
        op.add_column("matches", sa.Column("mvp_guest_id", sa.Integer(), nullable=True))

def downgrade():
    bind = op.get_bind()
    if _has_column(bind, "matches", "mvp_guest_id"):
        op.drop_column("matches", "mvp_guest_id")
    if _has_column(bind, "matches", "mvp_player_id"):
        op.drop_column("matches", "mvp_player_id")
