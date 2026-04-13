"""add groups fine_enabled

Revision ID: 0023_add_groups_fine_enabled
Revises: 0022_add_user_profile_fields
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0023_add_groups_fine_enabled"
down_revision = "0022_add_user_profile_fields"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    existing_columns = {col["name"] for col in inspector.get_columns("groups")}

    if "fine_enabled" not in existing_columns:
        op.add_column(
            "groups",
            sa.Column(
                "fine_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    op.execute("UPDATE groups SET fine_enabled = false WHERE fine_enabled IS NULL")
    op.alter_column("groups", "fine_enabled", nullable=False, server_default=sa.text("false"))
    op.alter_column("groups", "fine_enabled", server_default=None)


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    existing_columns = {col["name"] for col in inspector.get_columns("groups")}

    if "fine_enabled" in existing_columns:
        op.drop_column("groups", "fine_enabled")
