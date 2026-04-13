"""phase 8 finance advanced

Revision ID: 0043_phase8_finance_advanced
Revises: 0042_merge_phase7_heads
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa

revision = "0043_phase8_finance_advanced"
down_revision = "0042_merge_phase7_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE group_financial_entries
        ADD COLUMN IF NOT EXISTS paid_amount_cents INTEGER NOT NULL DEFAULT 0;
    """))
    op.execute(sa.text("""
        ALTER TABLE group_financial_entries
        ADD COLUMN IF NOT EXISTS payment_method VARCHAR(30);
    """))
    op.execute(sa.text("""
        ALTER TABLE group_financial_entries
        ADD COLUMN IF NOT EXISTS notes TEXT;
    """))
    op.execute(sa.text("""
        UPDATE group_financial_entries
        SET paid_amount_cents = COALESCE(amount_cents, 0)
        WHERE COALESCE(paid, false) = true AND COALESCE(paid_amount_cents, 0) = 0;
    """))


def downgrade() -> None:
    op.execute(sa.text("ALTER TABLE group_financial_entries DROP COLUMN IF EXISTS notes;"))
    op.execute(sa.text("ALTER TABLE group_financial_entries DROP COLUMN IF EXISTS payment_method;"))
    op.execute(sa.text("ALTER TABLE group_financial_entries DROP COLUMN IF EXISTS paid_amount_cents;"))
