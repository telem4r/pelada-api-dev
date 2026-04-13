"""enforce case-insensitive unique emails on users

Revision ID: 0070_users_email_lower_unique
Revises: 0069_match_settings_parity
Create Date: 2026-04-02
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0070_users_email_lower_unique"
down_revision = "0069_match_settings_parity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_users_email")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower
        ON public.users (lower(email))
        WHERE email IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.uq_users_email_lower")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON public.users(email)")
