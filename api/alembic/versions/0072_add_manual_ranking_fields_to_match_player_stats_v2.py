"""add manual ranking fields to match_player_stats_v2

Revision ID: 0072_add_manual_ranking_fields_to_match_player_stats_v2
Revises: 0071_add_user_consent_timestamps
Create Date: 2026-04-04 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = '0072_add_manual_ranking_fields_to_match_player_stats_v2'
down_revision = '0071_add_user_consent_timestamps'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('match_player_stats_v2', sa.Column('wins', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('match_player_stats_v2', sa.Column('fair_play', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('match_player_stats_v2', 'fair_play')
    op.drop_column('match_player_stats_v2', 'wins')
