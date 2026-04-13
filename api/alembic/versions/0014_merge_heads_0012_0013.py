"""merge heads 0012 and 0013

Revision ID: 0014_merge_heads_0012_0013
Revises: 0012_safe_ensure_groups_columns, 0013_safe_ensure_groups_columns
Create Date: 2026-02-25
"""

from alembic import op  # noqa: F401

revision = "0014_merge_heads_0012_0013"
down_revision = "0013_safe_ensure_groups_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:

    pass
