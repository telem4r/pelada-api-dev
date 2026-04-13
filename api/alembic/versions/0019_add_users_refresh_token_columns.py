"""add refresh_token columns to users (compat)

Revision ID: 0019_add_users_refresh_token_columns
Revises: 0018a_widen_alembic_version_num
Create Date: 2026-02-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_add_users_refresh_token_columns"
down_revision = "0018a_widen_alembic_version_num"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotente (pode rodar mais de uma vez)
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='users' AND column_name='refresh_token'
            ) THEN
                ALTER TABLE users ADD COLUMN refresh_token VARCHAR(255);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='users' AND column_name='refresh_token_expires_at'
            ) THEN
                ALTER TABLE users ADD COLUMN refresh_token_expires_at TIMESTAMPTZ;
            END IF;

            -- novo padrão: armazenar hash do refresh token
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='users' AND column_name='refresh_token_hash'
            ) THEN
                ALTER TABLE users ADD COLUMN refresh_token_hash VARCHAR(255);
            END IF;
        END $$;
        """
    )

    # Índices (não falha se já existir)
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_refresh_token ON users (refresh_token)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_refresh_token_hash ON users (refresh_token_hash)")


def downgrade() -> None:
    # downgrade conservador: não remove colunas (evita perda de dados)
    pass
