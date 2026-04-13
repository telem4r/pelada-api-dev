"""ensure groups columns exist (safe, idempotent)

Revision ID: 0012_safe_ensure_groups_columns
Revises: 0011_merge_heads_0009_0010
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_safe_ensure_groups_columns"
down_revision = "0011_add_user_profile_fields"
branch_labels = None
depends_on = None


def _table_exists(conn, table_name: str) -> bool:
    q = sa.text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema='public' AND table_name=:t
        )
        """
    )
    return bool(conn.execute(q, {"t": table_name}).scalar())


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    q = sa.text(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t AND column_name=:c
        )
        """
    )
    return bool(conn.execute(q, {"t": table_name, "c": column_name}).scalar())


def _add_column_if_missing(conn, table: str, col: sa.Column):
    if not _column_exists(conn, table, col.name):
        op.add_column(table, col)


def upgrade():
    conn = op.get_bind()

    if not _table_exists(conn, "groups"):
        return

    _add_column_if_missing(conn, "groups", sa.Column("country", sa.String(length=80), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("state", sa.String(length=80), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("city", sa.String(length=120), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("modality", sa.String(length=40), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("group_type", sa.String(length=20), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("gender_type", sa.String(length=20), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("payment_method", sa.String(length=20), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("payment_key", sa.String(length=255), nullable=True))

    _add_column_if_missing(conn, "groups", sa.Column("venue_cost", sa.Float(), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("per_person_cost", sa.Float(), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("monthly_cost", sa.Float(), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("single_cost", sa.Float(), nullable=True))

    _add_column_if_missing(
        conn,
        "groups",
        sa.Column("fine_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    _add_column_if_missing(conn, "groups", sa.Column("fine_amount", sa.Float(), nullable=True))
    _add_column_if_missing(conn, "groups", sa.Column("fine_reason", sa.String(length=255), nullable=True))


def downgrade():
    pass
