"""match: arrival/paid flags + per-match payment key/method

Revision ID: 0016_match_arrival_payment_and_payment_key
Revises: 0015_fix_groups_timestamps_defaults
Create Date: 2026-02-26
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_match_arrival_payment_and_payment_key"
down_revision = "0015_fix_groups_timestamps_defaults"
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

    # matches: payment_method / payment_key
    if _table_exists("matches"):
        if not _column_exists("matches", "payment_method"):
            op.add_column("matches", sa.Column("payment_method", sa.String(length=20), nullable=True))
        if not _column_exists("matches", "payment_key"):
            op.add_column("matches", sa.Column("payment_key", sa.String(length=255), nullable=True))

    # match_participants: arrived / paid
    if _table_exists("match_participants"):
        if not _column_exists("match_participants", "arrived"):
            op.add_column(
                "match_participants",
                sa.Column("arrived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )
        if not _column_exists("match_participants", "paid"):
            op.add_column(
                "match_participants",
                sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )

    # match_guests: arrived / paid
    if _table_exists("match_guests"):
        if not _column_exists("match_guests", "arrived"):
            op.add_column(
                "match_guests",
                sa.Column("arrived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )
        if not _column_exists("match_guests", "paid"):
            op.add_column(
                "match_guests",
                sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            )


def downgrade():
    # downgrade conservador (não remove para evitar perda)
    pass
