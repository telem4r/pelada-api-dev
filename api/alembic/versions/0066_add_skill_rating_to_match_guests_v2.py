"""add skill_rating to match_guests_v2

Revision ID: 0066_add_skill_rating_to_match_guests_v2
Revises: 0065_rebuild_core_tables_uuid
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0066_add_skill_rating_to_match_guests_v2"
down_revision = "0065_rebuild_core_tables_uuid"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(name, schema="public")


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c["name"] for c in insp.get_columns(table_name, schema="public")]
    return column_name in cols


def upgrade() -> None:
    if _table_exists("match_guests_v2") and not _column_exists("match_guests_v2", "skill_rating"):
        op.add_column("match_guests_v2", sa.Column("skill_rating", sa.Integer(), nullable=True), schema="public")
        op.execute("ALTER TABLE public.match_guests_v2 ADD CONSTRAINT ck_match_guests_v2_skill_rating CHECK (skill_rating IS NULL OR (skill_rating >= 1 AND skill_rating <= 5))")


def downgrade() -> None:
    if _table_exists("match_guests_v2") and _column_exists("match_guests_v2", "skill_rating"):
        op.drop_column("match_guests_v2", "skill_rating", schema="public")
