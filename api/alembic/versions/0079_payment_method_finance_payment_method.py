"""finance payment method

Revision ID: 0079_payment_method
Revises: 0078_communications
Create Date: 2026-10-31 16:59:44.488421

"""
from alembic import op

revision = '0079_payment_method'
down_revision = '0078_communications'
branch_labels = None
depends_on = None

SQL = "ALTER TABLE public.finance_entries_v2 ADD COLUMN IF NOT EXISTS payment_method varchar(80);"

def upgrade():
    op.execute(SQL)

def downgrade():
    op.execute("ALTER TABLE public.finance_entries_v2 DROP COLUMN IF EXISTS payment_method;")
