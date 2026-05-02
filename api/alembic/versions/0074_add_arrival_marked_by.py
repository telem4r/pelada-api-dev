"""add arrival_marked_by_user_id to match_participants_v2 and match_guests_v2

Revision ID: 0074_add_arrival_marked_by
Revises: 0073_fix_match_player_stats_v2_team_number_manual_ranking
Create Date: 2026-04-30 12:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0074_add_arrival_marked_by"
down_revision = "0073_fix_match_player_stats_v2_team_number_manual_ranking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'match_participants_v2'
                  AND column_name = 'arrival_marked_by_user_id'
            ) THEN
                ALTER TABLE public.match_participants_v2
                ADD COLUMN arrival_marked_by_user_id UUID NULL;
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'match_guests_v2'
                  AND column_name = 'arrival_marked_by_user_id'
            ) THEN
                ALTER TABLE public.match_guests_v2
                ADD COLUMN arrival_marked_by_user_id UUID NULL;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE public.match_participants_v2 DROP COLUMN IF EXISTS arrival_marked_by_user_id;")
    op.execute("ALTER TABLE public.match_guests_v2 DROP COLUMN IF EXISTS arrival_marked_by_user_id;")
