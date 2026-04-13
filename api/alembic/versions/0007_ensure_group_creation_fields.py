"""ensure group creation fields exist

Revision ID: 0007_ensure_group_fields
Revises: 0006_merge_heads_groups
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_ensure_group_fields"
down_revision = "0006_merge_heads_groups"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    # compatível com Postgres
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

    # garante que a tabela groups existe
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
        # Se por algum motivo o banco ainda não tem 'groups',
        # não tentamos criar aqui (0004 é o responsável).
        return

    cols = [
        ("country", sa.String(length=80)),
        ("state", sa.String(length=80)),
        ("city", sa.String(length=120)),
        ("modality", sa.String(length=40)),
        ("group_type", sa.String(length=20)),
        ("gender_type", sa.String(length=20)),
        ("payment_method", sa.String(length=20)),
    ]

    for name, coltype in cols:
        if not _column_exists(conn, "groups", name):
            op.add_column("groups", sa.Column(name, coltype, nullable=True))


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

    # remove apenas se existir
    for name in [
        "payment_method",
        "gender_type",
        "group_type",
        "modality",
        "city",
        "state",
        "country",
    ]:
        if _column_exists(conn, "groups", name):
            op.drop_column("groups", name)
