"""widen alembic_version.version_num to varchar(255) for long revision ids

Revision ID: 0018a_widen_alembic_version_num
Revises: 0018_fix_players_rating_default
Create Date: 2026-03-02
"""

from alembic import op

revision = "0018a_widen_alembic_version_num"
down_revision = "0018_fix_players_rating_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic por padrão usa VARCHAR(32) para version_num.
    # Como este projeto usa revision ids maiores (ex: 0019_add_users_refresh_token_columns),
    # precisamos garantir um tamanho maior para não quebrar upgrade/stamp em bancos novos.
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255);")


def downgrade() -> None:
    # Não reduzimos de volta para 32 para evitar risco de truncar histórico.
    pass
