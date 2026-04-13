"""add payment_key to groups

Revision ID: 0008_add_group_payment_key
Revises: 0007_ensure_group_fields
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_add_group_payment_key"
down_revision = "0007_ensure_group_fields"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    q = sa.text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = :table
          AND column_name = :column
        LIMIT 1
        """
    )
    return conn.execute(q, {"table": table, "column": column}).fetchone() is not None


def upgrade():
    conn = op.get_bind()

    table_exists = conn.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = 'groups'
            LIMIT 1
            """
        )
    ).fetchone() is not None

    if not table_exists:
        return

    if not _column_exists(conn, "groups", "payment_key"):
        op.add_column("groups", sa.Column("payment_key", sa.String(length=255), nullable=True))


def downgrade():
    conn = op.get_bind()

    table_exists = conn.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = 'groups'
            LIMIT 1
            """
        )
    ).fetchone() is not None

    if not table_exists:
        return

    if _column_exists(conn, "groups", "payment_key"):
        op.drop_column("groups", "payment_key")
