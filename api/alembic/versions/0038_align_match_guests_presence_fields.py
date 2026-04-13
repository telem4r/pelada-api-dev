"""align match_guests presence fields

Revision ID: 0038_align_match_guests_presence_fields
Revises: 0037_auto_align_match_tables
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0038_align_match_guests_presence_fields"
down_revision = "0037_auto_align_match_tables"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
              AND column_name = :column_name
            LIMIT 1
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    ).scalar()
    return bool(result)


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = :table_name
            LIMIT 1
            """
        ),
        {"table_name": table_name},
    ).scalar()
    return bool(result)


def upgrade():
    bind = op.get_bind()

    if not _table_exists("match_guests"):
        return

    if not _column_exists("match_guests", "status"):
        op.add_column("match_guests", sa.Column("status", sa.String(length=50), nullable=True))

    if not _column_exists("match_guests", "arrived"):
        op.add_column("match_guests", sa.Column("arrived", sa.Boolean(), nullable=True))

    if not _column_exists("match_guests", "no_show"):
        op.add_column("match_guests", sa.Column("no_show", sa.Boolean(), nullable=True))

    if not _column_exists("match_guests", "no_show_justified"):
        op.add_column("match_guests", sa.Column("no_show_justified", sa.Boolean(), nullable=True))

    if not _column_exists("match_guests", "no_show_reason"):
        op.add_column("match_guests", sa.Column("no_show_reason", sa.Text(), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE match_guests
            SET status = COALESCE(status, 'confirmed'),
                arrived = COALESCE(arrived, false),
                no_show = COALESCE(no_show, false),
                no_show_justified = COALESCE(no_show_justified, false)
            """
        )
    )

    # defaults de runtime para novas linhas
    bind.execute(sa.text("ALTER TABLE match_guests ALTER COLUMN status SET DEFAULT 'confirmed'"))
    bind.execute(sa.text("ALTER TABLE match_guests ALTER COLUMN arrived SET DEFAULT false"))
    bind.execute(sa.text("ALTER TABLE match_guests ALTER COLUMN no_show SET DEFAULT false"))
    bind.execute(sa.text("ALTER TABLE match_guests ALTER COLUMN no_show_justified SET DEFAULT false"))

    # endurece o schema após backfill
    op.alter_column("match_guests", "status", existing_type=sa.String(length=50), nullable=False)
    op.alter_column("match_guests", "arrived", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("match_guests", "no_show", existing_type=sa.Boolean(), nullable=False)
    op.alter_column("match_guests", "no_show_justified", existing_type=sa.Boolean(), nullable=False)

    # índice útil para leitura por partida/status
    bind.execute(
        sa.text(
            """
            CREATE INDEX IF NOT EXISTS ix_match_guests_match_id_status
            ON match_guests (match_id, status)
            """
        )
    )



def downgrade():
    # downgrade conservador: não remove colunas automaticamente
    # para evitar perda de dados em produção
    pass
