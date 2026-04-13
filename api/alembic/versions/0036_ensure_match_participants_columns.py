"""ensure match_participants columns required by current model exist

Revision ID: 0036_ensure_match_participants_columns
Revises: 0035_ensure_match_participants_player_id
Create Date: 2026-03-06
"""

from alembic import op

revision = "0036_ensure_match_participants_columns"
down_revision = "0035_ensure_match_participants_player_id"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS paid BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS no_show BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS no_show_justified BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS no_show_reason TEXT")
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS queue_position INTEGER")
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS waitlist_tier INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS arrived BOOLEAN NOT NULL DEFAULT false")

    op.execute("CREATE INDEX IF NOT EXISTS ix_match_participants_match_bucket_queue ON match_participants(match_id, status, waitlist_tier, queue_position)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_match_participants_match_bucket_queue")
    # colunas mantidas por segurança
