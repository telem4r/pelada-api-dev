"""add user consent timestamps

Revision ID: 0071_add_user_consent_timestamps
Revises: 0070_users_email_lower_unique
Create Date: 2026-04-03 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# 🔒 IDENTIFICAÇÃO CORRETA
revision = '0071_add_user_consent_timestamps'
down_revision = '0070_users_email_lower_unique'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        'users',
        sa.Column('privacy_accepted_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('users', 'privacy_accepted_at')
    op.drop_column('users', 'terms_accepted_at')