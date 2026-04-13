"""add match settings parity columns to matches_v2

Revision ID: 0069_match_settings_parity
Revises: 0068_match_features_parity
Create Date: 2026-03-29
"""
from alembic import op

revision = "0069_match_settings_parity"
down_revision = "0068_match_features_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col, typ, default in [
        ("city", "VARCHAR(120)", None),
        ("payment_method", "VARCHAR(40)", None),
        ("payment_key", "VARCHAR(255)", None),
        ("single_waitlist_release_days", "INTEGER", "0"),
        ("modality", "VARCHAR(50)", None),
        ("gender_type", "VARCHAR(30)", None),
        ("is_public", "BOOLEAN", "false"),
    ]:
        default_sql = f" DEFAULT {default}" if default is not None else ""
        op.execute(f"""
            DO $$ BEGIN
                ALTER TABLE public.matches_v2 ADD COLUMN IF NOT EXISTS {col} {typ}{default_sql};
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)


def downgrade() -> None:
    for col in [
        "gender_type",
        "modality",
        "single_waitlist_release_days",
        "payment_key",
        "payment_method",
        "city",
        "is_public",
    ]:
        op.execute(f"""
            DO $$ BEGIN
                ALTER TABLE public.matches_v2 DROP COLUMN IF EXISTS {col};
            EXCEPTION WHEN undefined_column THEN NULL;
            END $$;
        """)
