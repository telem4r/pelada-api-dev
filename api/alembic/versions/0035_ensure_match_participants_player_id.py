"""ensure match_participants.player_id exists and is backfilled

Revision ID: 0035_ensure_match_participants_player_id
Revises: 0034_add_match_participants_queue_fields
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa

revision = "0035_ensure_match_participants_player_id"
down_revision = "0034_add_match_participants_queue_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE match_participants ADD COLUMN IF NOT EXISTS player_id INTEGER")

    # se existir user_id legado, tenta backfill pelo player principal do usuário
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='match_participants' AND column_name='user_id'
        ) THEN
            UPDATE match_participants mp
            SET player_id = p.id
            FROM players p
            WHERE mp.player_id IS NULL
              AND mp.user_id = p.owner_id
              AND p.id = (
                  SELECT p2.id
                  FROM players p2
                  WHERE p2.owner_id = mp.user_id
                  ORDER BY p2.id ASC
                  LIMIT 1
              );
        END IF;
    END $$;
    """)

    # cria FK e índice se ainda não existirem
    op.execute("""
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'match_participants_player_id_fkey'
        ) THEN
            ALTER TABLE match_participants
            ADD CONSTRAINT match_participants_player_id_fkey
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE;
        END IF;
    END $$;
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_match_participants_player_id ON match_participants(player_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_match_participants_player_id")
    op.execute("ALTER TABLE match_participants DROP CONSTRAINT IF EXISTS match_participants_player_id_fkey")
    # não removemos a coluna por segurança de dados
