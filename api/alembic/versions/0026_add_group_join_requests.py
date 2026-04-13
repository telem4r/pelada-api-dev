"""add group join requests

Revision ID: 0026_add_group_join_requests
Revises: 0025_add_group_members_player_id
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0026_add_group_join_requests"
down_revision = "0025_add_group_members_player_id"
branch_labels = None
depends_on = None


def _clone_type(col_type):
    if isinstance(col_type, sa.Integer):
        return sa.Integer()
    if isinstance(col_type, sa.BigInteger):
        return sa.BigInteger()
    if isinstance(col_type, sa.String):
        return sa.String(length=col_type.length)
    return col_type.__class__()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    table_names = set(inspector.get_table_names())

    groups_cols = {col["name"]: col for col in inspector.get_columns("groups")}
    users_cols = {col["name"]: col for col in inspector.get_columns("users")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    user_id_type = _clone_type(users_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    if "group_join_requests" not in table_names:
        op.create_table(
            "group_join_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", user_id_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("player_id", player_id_type, sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("group_id", "player_id", name="uq_group_join_req_group_player"),
        )

    inspector = inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("group_join_requests")}

    if "ix_group_join_requests_group_id" not in indexes:
        op.create_index("ix_group_join_requests_group_id", "group_join_requests", ["group_id"])

    if "ix_group_join_requests_user_id" not in indexes:
        op.create_index("ix_group_join_requests_user_id", "group_join_requests", ["user_id"])

    if "ix_group_join_requests_player_id" not in indexes:
        op.create_index("ix_group_join_requests_player_id", "group_join_requests", ["player_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    table_names = set(inspector.get_table_names())
    if "group_join_requests" not in table_names:
        return

    indexes = {idx["name"] for idx in inspector.get_indexes("group_join_requests")}

    if "ix_group_join_requests_player_id" in indexes:
        op.drop_index("ix_group_join_requests_player_id", table_name="group_join_requests")

    if "ix_group_join_requests_user_id" in indexes:
        op.drop_index("ix_group_join_requests_user_id", table_name="group_join_requests")

    if "ix_group_join_requests_group_id" in indexes:
        op.drop_index("ix_group_join_requests_group_id", table_name="group_join_requests")

    op.drop_table("group_join_requests")
