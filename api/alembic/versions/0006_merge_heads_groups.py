"""merge heads 0004 and 0005

Revision ID: 0006_merge_heads_groups
Revises: 0005_group_creation_fields
Create Date: 2026-02-22
"""

from alembic import op  # noqa: F401

revision = "0006_merge_heads_groups"
down_revision = "0005_group_creation_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:

    # merge-only migration (no-op)
    pass
