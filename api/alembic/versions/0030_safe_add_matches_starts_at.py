"""safe add matches.starts_at (legacy DB compatibility)

Revision ID: 0030_safe_add_matches_starts_at
Revises: 0029_match_attendance_skill_and_requests
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_safe_add_matches_starts_at"
down_revision = "0029_match_attendance_skill_and_requests"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    def _table_exists(table: str) -> bool:
        q = sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:t
            )
            """
        )
        return bool(conn.execute(q, {"t": table}).scalar())

    def _column_exists(table: str, column: str) -> bool:
        q = sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t AND column_name=:c
            )
            """
        )
        return bool(conn.execute(q, {"t": table, "c": column}).scalar())

    if not _table_exists("matches"):
        return

    if _column_exists("matches", "starts_at"):
        return

    # 1) Add as nullable first (safe for existing rows)
    op.add_column("matches", sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True))

    # 2) Backfill from known legacy columns when present; otherwise use created_at/now()
    has_created_at = _column_exists("matches", "created_at")
    has_scheduled_at = _column_exists("matches", "scheduled_at")
    has_start_at = _column_exists("matches", "start_at")
    has_datetime = _column_exists("matches", "match_datetime")
    has_date = _column_exists("matches", "match_date") or _column_exists("matches", "date")

    # Build COALESCE expression in priority order
    coalesce_parts = []
    if has_scheduled_at:
        coalesce_parts.append("scheduled_at")
    if has_start_at:
        coalesce_parts.append("start_at")
    if has_datetime:
        coalesce_parts.append("match_datetime")
    if has_created_at:
        coalesce_parts.append("created_at")
    coalesce_parts.append("now()")

    expr = "COALESCE(" + ", ".join(coalesce_parts) + ")"

    # If we only have a date column, set time to 20:00 local-ish (arbitrary but stable)
    if has_date:
        # prefer match_date if exists, else date
        date_col = "match_date" if _column_exists("matches", "match_date") else "date"
        expr = f"COALESCE({expr}, ({date_col}::timestamp + time '20:00'))"

    conn.execute(sa.text(f"UPDATE matches SET starts_at = {expr} WHERE starts_at IS NULL"))

    # 3) Enforce NOT NULL going forward
    op.alter_column("matches", "starts_at", nullable=False, server_default=sa.text("now()"))

    # Optional: keep default for new inserts; application sets starts_at anyway.


def downgrade():
    # Conservative downgrade: do not drop columns to avoid data loss
    pass
