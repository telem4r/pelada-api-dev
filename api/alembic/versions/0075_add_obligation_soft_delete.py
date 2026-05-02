"""add soft-delete columns to finance_obligations_v2

Revision ID: 0075_add_obligation_soft_delete
Revises: 0074_add_arrival_marked_by
Create Date: 2026-04-30 21:00:00
"""

from alembic import op

revision = "0075_add_obligation_soft_delete"
down_revision = "0074_add_arrival_marked_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='finance_obligations_v2' AND column_name='deleted_at') THEN
                ALTER TABLE public.finance_obligations_v2 ADD COLUMN deleted_at TIMESTAMPTZ NULL;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='finance_obligations_v2' AND column_name='deleted_by_user_id') THEN
                ALTER TABLE public.finance_obligations_v2 ADD COLUMN deleted_by_user_id UUID NULL;
            END IF;
        END $$;
    """)
    # Update status check constraint to include 'excluida'
    op.execute("ALTER TABLE public.finance_obligations_v2 DROP CONSTRAINT IF EXISTS ck_fin_obligations_v2_status;")
    op.execute("ALTER TABLE public.finance_obligations_v2 ADD CONSTRAINT ck_fin_obligations_v2_status CHECK (status in ('aberta', 'parcial', 'paga', 'cancelada', 'excluida'));")


def downgrade() -> None:
    op.execute("ALTER TABLE public.finance_obligations_v2 DROP COLUMN IF EXISTS deleted_at;")
    op.execute("ALTER TABLE public.finance_obligations_v2 DROP COLUMN IF EXISTS deleted_by_user_id;")
    op.execute("ALTER TABLE public.finance_obligations_v2 DROP CONSTRAINT IF EXISTS ck_fin_obligations_v2_status;")
    op.execute("ALTER TABLE public.finance_obligations_v2 ADD CONSTRAINT ck_fin_obligations_v2_status CHECK (status in ('aberta', 'parcial', 'paga', 'cancelada'));")
