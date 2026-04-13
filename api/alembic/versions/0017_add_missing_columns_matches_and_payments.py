"""add missing columns: matches + payments

Revision ID: 0017_add_missing_columns_matches_and_payments
Revises: 0016_match_arrival_payment_and_payment_key
Create Date: 2026-02-27
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_add_missing_columns_matches_and_payments"
down_revision = "0016_match_arrival_payment_and_payment_key"
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

    # ----------------------------------------------------------
    # matches: columns expected by new API but missing on old DB
    # ----------------------------------------------------------
    if _table_exists("matches"):
        if not _column_exists("matches", "title"):
            op.add_column("matches", sa.Column("title", sa.String(length=120), nullable=True))

        if not _column_exists("matches", "status"):
            op.add_column(
                "matches",
                sa.Column(
                    "status",
                    sa.String(length=20),
                    nullable=False,
                    server_default=sa.text("'scheduled'"),
                ),
            )

        if not _column_exists("matches", "player_limit"):
            op.add_column(
                "matches",
                sa.Column(
                    "player_limit",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )

        if not _column_exists("matches", "price_cents"):
            op.add_column("matches", sa.Column("price_cents", sa.Integer(), nullable=True))

        if not _column_exists("matches", "currency"):
            op.add_column("matches", sa.Column("currency", sa.String(length=10), nullable=True))

        if not _column_exists("matches", "city"):
            op.add_column("matches", sa.Column("city", sa.String(length=120), nullable=True))

        if not _column_exists("matches", "location_name"):
            op.add_column("matches", sa.Column("location_name", sa.String(length=255), nullable=True))

        if not _column_exists("matches", "notes"):
            op.add_column("matches", sa.Column("notes", sa.Text(), nullable=True))

        if not _column_exists("matches", "is_public"):
            op.add_column(
                "matches",
                sa.Column(
                    "is_public",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )

    # ----------------------------------------------------------
    # payments: columns expected by new API but missing on old DB
    # ----------------------------------------------------------
    if _table_exists("payments"):
        if not _column_exists("payments", "kind"):
            op.add_column(
                "payments",
                sa.Column(
                    "kind",
                    sa.String(length=30),
                    nullable=False,
                    server_default=sa.text("'group'"),
                ),
            )

        if not _column_exists("payments", "description"):
            op.add_column("payments", sa.Column("description", sa.Text(), nullable=True))

        if not _column_exists("payments", "paid"):
            op.add_column(
                "payments",
                sa.Column(
                    "paid",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            )

        if not _column_exists("payments", "paid_at"):
            op.add_column("payments", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))

        if not _column_exists("payments", "created_at"):
            op.add_column(
                "payments",
                sa.Column(
                    "created_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.text("now()"),
                ),
            )

        if not _column_exists("payments", "updated_at"):
            op.add_column(
                "payments",
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.text("now()"),
                ),
            )


def downgrade():
    # downgrade conservador (não remove colunas para evitar perda de dados)
    pass
