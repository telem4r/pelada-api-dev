"""add user profile fields

Revision ID: 0022_add_user_profile_fields
Revises: 0021_add_refresh_token_hash_column
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0022_add_user_profile_fields"
down_revision = "0021_add_refresh_token_hash_column"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    wanted_columns = [
        ("first_name", sa.String(length=100)),
        ("last_name", sa.String(length=100)),
        ("birth_date", sa.Date()),
        ("favorite_team", sa.String(length=120)),
        ("birth_country", sa.String(length=100)),
        ("birth_state", sa.String(length=100)),
        ("birth_city", sa.String(length=120)),
        ("current_country", sa.String(length=100)),
        ("current_state", sa.String(length=100)),
        ("current_city", sa.String(length=120)),
        ("position", sa.String(length=80)),
        ("preferred_foot", sa.String(length=20)),
        ("language", sa.String(length=10)),
    ]

    for column_name, column_type in wanted_columns:
        if column_name not in existing_columns:
            op.add_column("users", sa.Column(column_name, column_type, nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    for column_name in [
        "language",
        "preferred_foot",
        "position",
        "current_city",
        "current_state",
        "current_country",
        "birth_city",
        "birth_state",
        "birth_country",
        "favorite_team",
        "birth_date",
        "last_name",
        "first_name",
    ]:
        if column_name in existing_columns:
            op.drop_column("users", column_name)
