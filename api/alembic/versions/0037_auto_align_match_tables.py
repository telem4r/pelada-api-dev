"""auto align match guests and participants

Revision ID: 0037_auto_align_match_tables
Revises: 0036_ensure_match_participants_columns
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0037_auto_align_match_tables"
down_revision = "0036_ensure_match_participants_columns"
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

    # -------------------------------------------------
    # match_guests
    # -------------------------------------------------
    if _table_exists("match_guests"):
        if not _column_exists("match_guests", "group_id"):
            op.add_column("match_guests", sa.Column("group_id", sa.String(length=36), nullable=True))

        if not _column_exists("match_guests", "position"):
            op.add_column("match_guests", sa.Column("position", sa.String(length=50), nullable=True))

        if not _column_exists("match_guests", "skill_rating"):
            op.add_column("match_guests", sa.Column("skill_rating", sa.Integer(), nullable=True))

        if not _column_exists("match_guests", "created_by_user_id"):
            op.add_column("match_guests", sa.Column("created_by_user_id", sa.Integer(), nullable=True))

        if not _column_exists("match_guests", "created_at"):
            op.add_column(
                "match_guests",
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            )

        if not _column_exists("match_guests", "updated_at"):
            op.add_column(
                "match_guests",
                sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            )

        # backfill group_id a partir de matches.group_id
        if _column_exists("match_guests", "group_id"):
            bind.execute(
                sa.text(
                    """
                    UPDATE match_guests mg
                    SET group_id = m.group_id
                    FROM matches m
                    WHERE mg.match_id = m.id
                      AND mg.group_id IS NULL
                    """
                )
            )

        # check 1..5 para skill_rating
        bind.execute(
            sa.text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'ck_match_guests_skill_rating_1_5'
                    ) THEN
                        ALTER TABLE match_guests
                        ADD CONSTRAINT ck_match_guests_skill_rating_1_5
                        CHECK (skill_rating IS NULL OR (skill_rating >= 1 AND skill_rating <= 5));
                    END IF;
                END$$;
                """
            )
        )

    # -------------------------------------------------
    # match_participants
    # -------------------------------------------------
    if _table_exists("match_participants"):
        if not _column_exists("match_participants", "player_id"):
            op.add_column("match_participants", sa.Column("player_id", sa.Integer(), nullable=True))

        if not _column_exists("match_participants", "arrived"):
            op.add_column(
                "match_participants",
                sa.Column("arrived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )

        if not _column_exists("match_participants", "paid"):
            op.add_column(
                "match_participants",
                sa.Column("paid", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )

        if not _column_exists("match_participants", "no_show"):
            op.add_column(
                "match_participants",
                sa.Column("no_show", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )

        if not _column_exists("match_participants", "no_show_justified"):
            op.add_column(
                "match_participants",
                sa.Column("no_show_justified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )

        if not _column_exists("match_participants", "no_show_reason"):
            op.add_column("match_participants", sa.Column("no_show_reason", sa.Text(), nullable=True))

        if not _column_exists("match_participants", "queue_position"):
            op.add_column("match_participants", sa.Column("queue_position", sa.Integer(), nullable=True))

        if not _column_exists("match_participants", "waitlist_tier"):
            op.add_column(
                "match_participants",
                sa.Column("waitlist_tier", sa.Integer(), server_default=sa.text("0"), nullable=False),
            )

        if not _column_exists("match_participants", "requires_approval"):
            op.add_column(
                "match_participants",
                sa.Column("requires_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            )

        if not _column_exists("match_participants", "created_at"):
            op.add_column(
                "match_participants",
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            )

        if not _column_exists("match_participants", "updated_at"):
            op.add_column(
                "match_participants",
                sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
            )

        # backfill player_id a partir de group_members.player_id quando houver relação por user_id
        if _column_exists("match_participants", "player_id") and _column_exists("match_participants", "user_id"):
            bind.execute(
                sa.text(
                    """
                    UPDATE match_participants mp
                    SET player_id = gm.player_id
                    FROM matches m, group_members gm
                    WHERE mp.match_id = m.id
                      AND gm.group_id = m.group_id
                      AND gm.user_id = mp.user_id
                      AND mp.player_id IS NULL
                      AND gm.player_id IS NOT NULL
                    """
                )
            )

        # índices úteis
        bind.execute(
            sa.text(
                """
                CREATE INDEX IF NOT EXISTS ix_match_participants_match_id_status
                ON match_participants (match_id, status)
                """
            )
        )

        bind.execute(
            sa.text(
                """
                CREATE INDEX IF NOT EXISTS ix_match_participants_match_id_queue
                ON match_participants (match_id, waitlist_tier, queue_position)
                """
            )
        )

    # -------------------------------------------------
    # normalização opcional de timestamps nullable -> preenchidos
    # -------------------------------------------------
    if _table_exists("match_guests") and _column_exists("match_guests", "created_at"):
        bind.execute(sa.text("UPDATE match_guests SET created_at = now() WHERE created_at IS NULL"))
    if _table_exists("match_guests") and _column_exists("match_guests", "updated_at"):
        bind.execute(sa.text("UPDATE match_guests SET updated_at = now() WHERE updated_at IS NULL"))

    if _table_exists("match_participants") and _column_exists("match_participants", "created_at"):
        bind.execute(sa.text("UPDATE match_participants SET created_at = now() WHERE created_at IS NULL"))
    if _table_exists("match_participants") and _column_exists("match_participants", "updated_at"):
        bind.execute(sa.text("UPDATE match_participants SET updated_at = now() WHERE updated_at IS NULL"))


def downgrade():
    # downgrade conservador: não remove colunas automaticamente
    # para evitar perda de dados em produção
    pass
