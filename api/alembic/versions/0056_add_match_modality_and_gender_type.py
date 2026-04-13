"""add modality and gender_type to matches

Revision ID: 0056_add_match_modality_and_gender_type
Revises: 0055_match_position_slots
Create Date: 2026-03-20 19:25:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0056_add_match_modality_and_gender_type"
down_revision = "0055_match_position_slots"
branch_labels = None
depends_on = None


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not _has_column(inspector, "matches", "modality"):
        op.add_column("matches", sa.Column("modality", sa.String(length=50), nullable=True))
    if not _has_column(inspector, "matches", "gender_type"):
        op.add_column("matches", sa.Column("gender_type", sa.String(length=30), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if _has_column(inspector, "matches", "gender_type"):
        op.drop_column("matches", "gender_type")
    if _has_column(inspector, "matches", "modality"):
        op.drop_column("matches", "modality")
