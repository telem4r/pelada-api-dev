"""phase11 social and geolocation

Revision ID: 0046_phase11_social_and_geo
Revises: 0045_phase10_communication_and_notifications
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa

revision = '0046_phase11_social_and_geo'
down_revision = '0045_phase10_communication_and_notifications'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('matches', sa.Column('location_lat', sa.Float(), nullable=True))
    op.add_column('matches', sa.Column('location_lng', sa.Float(), nullable=True))

    op.create_table(
        'player_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('bio', sa.Text(), nullable=True),
        sa.Column('city', sa.String(length=120), nullable=True),
        sa.Column('avatar_url', sa.String(length=500), nullable=True),
        sa.Column('main_position', sa.String(length=80), nullable=True),
        sa.Column('skill_level', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('player_id', name='uq_player_profiles_player_id'),
    )
    op.create_index(op.f('ix_player_profiles_player_id'), 'player_profiles', ['player_id'], unique=True)

    op.create_table(
        'player_network',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('connected_player_id', sa.Integer(), nullable=False),
        sa.Column('shared_matches_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('invited_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_played_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['connected_player_id'], ['players.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('player_id', 'connected_player_id', name='uq_player_network_pair'),
    )
    op.create_index(op.f('ix_player_network_player_id'), 'player_network', ['player_id'], unique=False)
    op.create_index(op.f('ix_player_network_connected_player_id'), 'player_network', ['connected_player_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_player_network_connected_player_id'), table_name='player_network')
    op.drop_index(op.f('ix_player_network_player_id'), table_name='player_network')
    op.drop_table('player_network')

    op.drop_index(op.f('ix_player_profiles_player_id'), table_name='player_profiles')
    op.drop_table('player_profiles')

    op.drop_column('matches', 'location_lng')
    op.drop_column('matches', 'location_lat')
