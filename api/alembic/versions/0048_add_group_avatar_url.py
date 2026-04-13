"""add group avatar url

Revision ID: 0048_add_group_avatar_url
Revises: 0047_add_missing_payments_owner_id
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

revision = '0048_add_group_avatar_url'
down_revision = '0047_add_missing_payments_owner_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('groups') as batch_op:
        batch_op.add_column(sa.Column('avatar_url', sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('groups') as batch_op:
        batch_op.drop_column('avatar_url')
