"""fix groups fine columns (apply real DDL)

Revision ID: 0024_fix_groups_fine_columns
Revises: 0023_add_groups_fine_enabled
Create Date: 2026-03-03
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0024_fix_groups_fine_columns"
down_revision = "0023_add_groups_fine_enabled"
branch_labels = None
depends_on = None


def upgrade():
    # ✅ seguro: roda mesmo se já existir
    op.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS fine_enabled BOOLEAN NOT NULL DEFAULT FALSE;")
    op.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS fine_amount DOUBLE PRECISION;")
    op.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS fine_reason TEXT;")


def downgrade():
    # cuidado: downgrade removendo colunas pode perder dados
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS fine_reason;")
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS fine_amount;")
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS fine_enabled;")
