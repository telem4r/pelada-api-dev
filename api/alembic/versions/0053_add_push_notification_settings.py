"""add push notification settings

Revision ID: 0053_add_push_notification_settings
Revises: 0052_phase11_friends_feed_reputation_refine
Create Date: 2026-03-17 22:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = '0053_add_push_notification_settings'
down_revision = '0052_phase11_friends_feed_reputation_refine'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('notification_settings') as batch_op:
        batch_op.add_column(sa.Column('push_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.add_column(sa.Column('push_matches_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.add_column(sa.Column('push_finance_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.add_column(sa.Column('push_announcements_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.add_column(sa.Column('push_comments_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.add_column(sa.Column('push_invites_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.add_column(sa.Column('push_fines_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')))
        batch_op.add_column(sa.Column('push_token', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('push_platform', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('push_token_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('notification_settings') as batch_op:
        batch_op.drop_column('push_token_updated_at')
        batch_op.drop_column('push_platform')
        batch_op.drop_column('push_token')
        batch_op.drop_column('push_fines_enabled')
        batch_op.drop_column('push_invites_enabled')
        batch_op.drop_column('push_comments_enabled')
        batch_op.drop_column('push_announcements_enabled')
        batch_op.drop_column('push_finance_enabled')
        batch_op.drop_column('push_matches_enabled')
        batch_op.drop_column('push_enabled')
