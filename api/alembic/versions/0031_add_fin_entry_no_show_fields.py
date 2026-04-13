"""add no-show fields to group_financial_entries (safe)

Revision ID: 0031_add_fin_entry_no_show_fields
Revises: 0030_safe_add_matches_starts_at
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0031_add_fin_entry_no_show_fields"
down_revision = "0030_safe_add_matches_starts_at"
branch_labels = None
depends_on = None


def upgrade():
    # Alguns bancos legados criaram group_financial_entries sem os campos de no-show.
    # Adicionamos de forma segura (Postgres IF NOT EXISTS).
    op.execute(sa.text("""
        ALTER TABLE group_financial_entries
        ADD COLUMN IF NOT EXISTS no_show BOOLEAN NOT NULL DEFAULT false;
    """))

    op.execute(sa.text("""
        ALTER TABLE group_financial_entries
        ADD COLUMN IF NOT EXISTS no_show_justified BOOLEAN NOT NULL DEFAULT false;
    """))

    op.execute(sa.text("""
        ALTER TABLE group_financial_entries
        ADD COLUMN IF NOT EXISTS no_show_reason TEXT;
    """))


def downgrade():
    op.execute(sa.text("""
        ALTER TABLE group_financial_entries DROP COLUMN IF EXISTS no_show_reason;
    """))
    op.execute(sa.text("""
        ALTER TABLE group_financial_entries DROP COLUMN IF EXISTS no_show_justified;
    """))
    op.execute(sa.text("""
        ALTER TABLE group_financial_entries DROP COLUMN IF EXISTS no_show;
    """))
