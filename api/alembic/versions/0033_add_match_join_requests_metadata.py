"""add match join requests metadata

Revision ID: 0033_add_match_join_requests_metadata
Revises: 0032_align_matches_legacy_schema
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa  # noqa: F401


# revision identifiers, used by Alembic.
revision = "0033_add_match_join_requests_metadata"
down_revision = "0032_align_matches_legacy_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres supports IF NOT EXISTS, so we can be defensive even across legacy DBs.
    op.execute("ALTER TABLE match_join_requests ADD COLUMN IF NOT EXISTS group_id VARCHAR(36)")
    op.execute("ALTER TABLE match_join_requests ADD COLUMN IF NOT EXISTS message TEXT")
    op.execute("ALTER TABLE match_join_requests ADD COLUMN IF NOT EXISTS reviewed_by_user_id INTEGER")
    op.execute("ALTER TABLE match_join_requests ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ")

    # Best-effort FK/indexes (guarded)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'fk_match_join_requests_reviewed_by_user'
            ) THEN
                ALTER TABLE match_join_requests
                ADD CONSTRAINT fk_match_join_requests_reviewed_by_user
                FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id) ON DELETE SET NULL;
            END IF;
        END$$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes WHERE indexname = 'ix_match_join_requests_group_id'
            ) THEN
                CREATE INDEX ix_match_join_requests_group_id ON match_join_requests(group_id);
            END IF;
        END$$;
        """
    )

    # Normalize legacy statuses (older code used "approved")
    op.execute("UPDATE match_join_requests SET status='active' WHERE lower(status)='approved'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_match_join_requests_group_id")
    op.execute("ALTER TABLE match_join_requests DROP CONSTRAINT IF EXISTS fk_match_join_requests_reviewed_by_user")
    op.execute("ALTER TABLE match_join_requests DROP COLUMN IF EXISTS reviewed_at")
    op.execute("ALTER TABLE match_join_requests DROP COLUMN IF EXISTS reviewed_by_user_id")
    op.execute("ALTER TABLE match_join_requests DROP COLUMN IF EXISTS message")
    op.execute("ALTER TABLE match_join_requests DROP COLUMN IF EXISTS group_id")
