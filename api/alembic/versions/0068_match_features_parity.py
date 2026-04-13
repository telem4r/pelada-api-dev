"""Add match join requests v2, no-show tracking, geo columns, and group search support.

Revision ID: 0068_match_features_parity
Revises: 0067_communication_social_tables
Create Date: 2026-03-25

Adds:
- match_join_requests_v2 table
- no_show columns to match_participants_v2 and match_guests_v2
- location_lat/location_lng to matches_v2 (for nearby)
- is_public to groups (for search)
- match close/cancel status support
"""
from alembic import op
import sqlalchemy as sa

revision = "0068_match_features_parity"
down_revision = "0067_communication_social_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── Match Join Requests V2 ───────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.match_join_requests_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            match_id        UUID NOT NULL REFERENCES public.matches_v2(id) ON DELETE CASCADE,
            group_id        UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            requester_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            requester_player_id UUID REFERENCES public.players(id) ON DELETE SET NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(match_id, requester_user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_match_join_req_v2_match ON public.match_join_requests_v2(match_id)")

    # ── No-show columns on participants ──────────────────────────────
    for col in ['no_show', 'no_show_justified']:
        for table in ['match_participants_v2', 'match_guests_v2']:
            op.execute(f"""
                DO $$ BEGIN
                    ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS {col} BOOLEAN NOT NULL DEFAULT false;
                EXCEPTION WHEN duplicate_column THEN NULL;
                END $$;
            """)
    for table in ['match_participants_v2', 'match_guests_v2']:
        op.execute(f"""
            DO $$ BEGIN
                ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS no_show_reason TEXT;
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)

    # ── Geo columns on matches_v2 ────────────────────────────────────
    for col, typ in [('location_lat', 'DOUBLE PRECISION'), ('location_lng', 'DOUBLE PRECISION')]:
        op.execute(f"""
            DO $$ BEGIN
                ALTER TABLE public.matches_v2 ADD COLUMN IF NOT EXISTS {col} {typ};
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)

    # ── is_public on groups ──────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE public.groups ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT false;
        EXCEPTION WHEN duplicate_column THEN NULL;
        END $$;
    """)

    # ── Ensure 'cancelled' is valid in match status enum ─────────────
    # The V2 enum already has 'cancelled', just ensure the column allows it
    # Also allow 'closed' as a status for close-with-charges flow
    op.execute("""
        DO $$ BEGIN
            ALTER TYPE match_status_enum_v2 ADD VALUE IF NOT EXISTS 'closed';
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.match_join_requests_v2 CASCADE")
    # Column drops are risky, skip for downgrade
