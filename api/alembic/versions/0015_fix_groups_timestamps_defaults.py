"""fix groups created_at/updated_at defaults

Revision ID: 0015_fix_groups_timestamps_defaults
Revises: 0014_merge_heads_0012_0013
Create Date: 2026-02-25
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "0015_fix_groups_timestamps_defaults"
down_revision = "0014_merge_heads_0012_0013"
branch_labels = None
depends_on = None


def upgrade():
    # 1) Preenche qualquer NULL antigo (se existir) para não quebrar o NOT NULL
    op.execute("UPDATE groups SET created_at = NOW() WHERE created_at IS NULL;")
    op.execute("UPDATE groups SET updated_at = NOW() WHERE updated_at IS NULL;")

    # 2) Garante DEFAULT no banco para inserts futuros (o seu INSERT não envia created_at)
    op.execute("ALTER TABLE groups ALTER COLUMN created_at SET DEFAULT NOW();")
    op.execute("ALTER TABLE groups ALTER COLUMN updated_at SET DEFAULT NOW();")


def downgrade():
    # Remove defaults
    op.execute("ALTER TABLE groups ALTER COLUMN created_at DROP DEFAULT;")
    op.execute("ALTER TABLE groups ALTER COLUMN updated_at DROP DEFAULT;")
