"""add users.refresh_token_hash

Revision ID: 0021_add_refresh_token_hash_column
Revises: 0020_add_missing_user_profile_columns
Create Date: 2026-03-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0021_add_refresh_token_hash_column"
down_revision = "0020_add_missing_user_profile_columns"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    columns = {col["name"] for col in inspector.get_columns("users")}
    indexes = {idx["name"] for idx in inspector.get_indexes("users")}

    if "refresh_token_hash" not in columns:
        op.add_column(
            "users",
            sa.Column("refresh_token_hash", sa.String(length=255), nullable=True),
        )

    if "ix_users_refresh_token_hash" not in indexes:
        op.create_index(
            "ix_users_refresh_token_hash",
            "users",
            ["refresh_token_hash"],
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    columns = {col["name"] for col in inspector.get_columns("users")}
    indexes = {idx["name"] for idx in inspector.get_indexes("users")}

    if "ix_users_refresh_token_hash" in indexes:
        op.drop_index("ix_users_refresh_token_hash", table_name="users")

    if "refresh_token_hash" in columns:
        op.drop_column("users", "refresh_token_hash")
