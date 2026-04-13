"""matches v2 operation locks and draw balance

Revision ID: 0059_matches_v2_operation_locks_and_draw_balance
Revises: 0058_matches_v2_draw_and_arrival
Create Date: 2026-03-21
"""

from alembic import op

revision = "0059_matches_v2_operation_locks_and_draw_balance"
down_revision = "0058_matches_v2_draw_and_arrival"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        alter table public.matches_v2
        add column if not exists roster_locked boolean not null default false
    """)
    op.execute("""
        alter table public.matches_v2
        add column if not exists draw_locked boolean not null default false
    """)


def downgrade() -> None:
    op.execute("alter table public.matches_v2 drop column if exists draw_locked")
    op.execute("alter table public.matches_v2 drop column if exists roster_locked")
