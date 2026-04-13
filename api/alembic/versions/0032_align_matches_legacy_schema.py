"""align matches table to current model (safe for legacy)

Revision ID: 0032_align_matches_legacy_schema
Revises: 0031_add_fin_entry_no_show_fields
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0032_align_matches_legacy_schema"
down_revision = "0031_add_fin_entry_no_show_fields"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    q = sa.text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
        LIMIT 1
        """
    )
    return conn.execute(q, {"t": table, "c": column}).first() is not None


def upgrade():
    conn = op.get_bind()

    # --- Add missing columns expected by SQLAlchemy model (safe) ---
    # NOTE: We keep legacy columns (date_time, value_per_player, venue_name) to avoid breaking old data.
    if not _has_column(conn, "matches", "title"):
        op.add_column("matches", sa.Column("title", sa.String(length=120), nullable=True))

    # home/away team ids are optional for now (many legacy DBs don't have teams populated)
    if not _has_column(conn, "matches", "home_team_id"):
        op.add_column("matches", sa.Column("home_team_id", sa.Integer(), nullable=True))
        op.create_index("ix_matches_home_team_id", "matches", ["home_team_id"])

    if not _has_column(conn, "matches", "away_team_id"):
        op.add_column("matches", sa.Column("away_team_id", sa.Integer(), nullable=True))
        op.create_index("ix_matches_away_team_id", "matches", ["away_team_id"])

    if not _has_column(conn, "matches", "price_cents"):
        op.add_column("matches", sa.Column("price_cents", sa.Integer(), nullable=True))

    if not _has_column(conn, "matches", "currency"):
        op.add_column("matches", sa.Column("currency", sa.String(length=10), nullable=True))

    if not _has_column(conn, "matches", "location_name"):
        op.add_column("matches", sa.Column("location_name", sa.String(length=255), nullable=True))

    if not _has_column(conn, "matches", "payment_method"):
        op.add_column("matches", sa.Column("payment_method", sa.String(length=20), nullable=True))

    if not _has_column(conn, "matches", "payment_key"):
        op.add_column("matches", sa.Column("payment_key", sa.String(length=255), nullable=True))

    # --- Backfill from legacy columns when present ---
    # starts_at: prefer existing starts_at, else legacy date_time, else created_at, else now()
    if _has_column(conn, "matches", "starts_at"):
        if _has_column(conn, "matches", "date_time"):
            op.execute(sa.text(
                """
                UPDATE matches
                   SET starts_at = COALESCE(starts_at, date_time, created_at, now())
                 WHERE starts_at IS NULL
                """
            ))
        else:
            op.execute(sa.text(
                """
                UPDATE matches
                   SET starts_at = COALESCE(starts_at, created_at, now())
                 WHERE starts_at IS NULL
                """
            ))

    # location_name from legacy venue_name
    if _has_column(conn, "matches", "location_name") and _has_column(conn, "matches", "venue_name"):
        op.execute(sa.text(
            """
            UPDATE matches
               SET location_name = COALESCE(location_name, venue_name)
             WHERE location_name IS NULL
            """
        ))

    # price_cents from legacy value_per_player (double precision)
    if _has_column(conn, "matches", "price_cents") and _has_column(conn, "matches", "value_per_player"):
        op.execute(sa.text(
            """
            UPDATE matches
               SET price_cents = COALESCE(price_cents, ROUND(value_per_player * 100)::int)
             WHERE price_cents IS NULL AND value_per_player IS NOT NULL
            """
        ))

    # title default
    if _has_column(conn, "matches", "title"):
        op.execute(sa.text(
            """
            UPDATE matches
               SET title = COALESCE(NULLIF(title, ''), 'Partida')
             WHERE title IS NULL OR title = ''
            """
        ))


def downgrade():
    # Safe downgrade: drop only the columns we added (if they exist).
    conn = op.get_bind()

    for idx_name in ["ix_matches_home_team_id", "ix_matches_away_team_id"]:
        try:
            op.drop_index(idx_name, table_name="matches")
        except Exception:
            pass

    for col in ["payment_key", "payment_method", "location_name", "currency", "price_cents", "away_team_id", "home_team_id", "title"]:
        if _has_column(conn, "matches", col):
            op.drop_column("matches", col)
