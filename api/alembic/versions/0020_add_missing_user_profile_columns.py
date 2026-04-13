"""add missing users columns avatar_url + language (safe)

Revision ID: 0020_add_missing_user_profile_columns
Revises: 0019_add_users_refresh_token_columns
Create Date: 2026-03-01
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "0020_add_missing_user_profile_columns"
down_revision = "0019_add_users_refresh_token_columns"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres: IF NOT EXISTS evita crash caso já exista (ambiente antigo)
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500);")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS language VARCHAR(10);")


def downgrade():
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS language;")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS avatar_url;")
