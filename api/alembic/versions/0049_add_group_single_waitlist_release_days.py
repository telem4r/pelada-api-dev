"""add group single waitlist release days

Revision ID: 0049_add_group_single_waitlist_release_days
Revises: 0048_add_group_avatar_url
Create Date: 2026-03-14
"""
from alembic import op
import sqlalchemy as sa

revision = '0049_add_group_single_waitlist_release_days'
down_revision = '0048_add_group_avatar_url'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('groups', sa.Column('single_waitlist_release_days', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('groups', 'single_waitlist_release_days', server_default=None)


def downgrade():
    op.drop_column('groups', 'single_waitlist_release_days')
