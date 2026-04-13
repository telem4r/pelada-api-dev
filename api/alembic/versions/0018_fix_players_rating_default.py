"""fix players.rating default + backfill for legacy users

Revision ID: 0018_fix_players_rating_default
Revises: 0017_add_missing_columns_matches_and_payments
Create Date: 2026-02-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_fix_players_rating_default"
down_revision = "0017_add_missing_columns_matches_and_payments"
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

    if not _table_exists("players"):
        return

    # se não existir, cria
    if not _column_exists("players", "rating"):
        op.add_column(
            "players",
            sa.Column("rating", sa.Integer(), nullable=False, server_default=sa.text("3")),
        )
        return

    # se existir, garante default no servidor
    op.execute(sa.text("ALTER TABLE players ALTER COLUMN rating SET DEFAULT 3"))

    # backfill de nulos
    op.execute(sa.text("UPDATE players SET rating = 3 WHERE rating IS NULL"))

    # garante NOT NULL
    op.execute(sa.text("ALTER TABLE players ALTER COLUMN rating SET NOT NULL"))


def downgrade():
    # downgrade conservador
    pass
