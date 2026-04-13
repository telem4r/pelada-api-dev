"""add match slots and participant position

Revision ID: 0055_match_position_slots
Revises: 0054_add_players_jsonb_to_match_draw_teams
Create Date: 2026-03-20
"""
from alembic import op
import sqlalchemy as sa

revision = '0055_match_position_slots'
down_revision = '0054_add_players_jsonb_to_match_draw_teams'
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col['name'] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_column(inspector, 'matches', 'line_slots'):
        op.add_column('matches', sa.Column('line_slots', sa.Integer(), nullable=False, server_default='0'))
    if not _has_column(inspector, 'matches', 'goalkeeper_slots'):
        op.add_column('matches', sa.Column('goalkeeper_slots', sa.Integer(), nullable=False, server_default='0'))
    if not _has_column(inspector, 'match_participants', 'position'):
        op.add_column('match_participants', sa.Column('position', sa.String(length=20), nullable=True))

    op.execute(sa.text('UPDATE matches SET line_slots = COALESCE(NULLIF(line_slots, 0), player_limit, 0)'))
    op.execute(sa.text('UPDATE matches SET goalkeeper_slots = COALESCE(goalkeeper_slots, 0)'))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_column(inspector, 'match_participants', 'position'):
        op.drop_column('match_participants', 'position')
    if _has_column(inspector, 'matches', 'goalkeeper_slots'):
        op.drop_column('matches', 'goalkeeper_slots')
    if _has_column(inspector, 'matches', 'line_slots'):
        op.drop_column('matches', 'line_slots')
