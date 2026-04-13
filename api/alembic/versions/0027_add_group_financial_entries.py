"""add group financial entries

Revision ID: 0027_add_group_financial_entries
Revises: 0026_add_group_join_requests
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0027_add_group_financial_entries"
down_revision = "0026_add_group_join_requests"
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
    group_members_cols = {col["name"]: col for col in inspector.get_columns("group_members")}
    users_cols = {col["name"]: col for col in inspector.get_columns("users")}
    matches_cols = {col["name"]: col for col in inspector.get_columns("matches")}

    # 1) groups.payment_due_day
    if "payment_due_day" not in groups_cols:
        op.add_column("groups", sa.Column("payment_due_day", sa.Integer(), nullable=True))

    # 2) group_members.billing_type
    if "billing_type" not in group_members_cols:
        op.add_column(
            "group_members",
            sa.Column("billing_type", sa.String(length=20), nullable=False, server_default="single"),
        )

    # 3) criar tabela group_financial_entries com tipos compatíveis com o schema real
    group_id_type = _clone_type(groups_cols["id"]["type"])
    user_id_type = _clone_type(users_cols["id"]["type"])
    match_id_type = _clone_type(matches_cols["id"]["type"])

    if "group_financial_entries" not in table_names:
        op.create_table(
            "group_financial_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", user_id_type, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("match_id", match_id_type, sa.ForeignKey("matches.id", ondelete="SET NULL"), nullable=True),
            sa.Column("entry_type", sa.String(length=20), nullable=False, server_default="manual"),
            sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="BRL"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_by_user_id", user_id_type, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

    inspector = inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("group_financial_entries")}

    if "ix_group_financial_entries_group_id" not in indexes:
        op.create_index("ix_group_financial_entries_group_id", "group_financial_entries", ["group_id"])

    if "ix_group_financial_entries_user_id" not in indexes:
        op.create_index("ix_group_financial_entries_user_id", "group_financial_entries", ["user_id"])

    if "ix_group_financial_entries_match_id" not in indexes:
        op.create_index("ix_group_financial_entries_match_id", "group_financial_entries", ["match_id"])

    if "ix_group_financial_entries_status" not in indexes:
        op.create_index("ix_group_financial_entries_status", "group_financial_entries", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    table_names = set(inspector.get_table_names())

    if "group_financial_entries" in table_names:
        indexes = {idx["name"] for idx in inspector.get_indexes("group_financial_entries")}

        if "ix_group_financial_entries_status" in indexes:
            op.drop_index("ix_group_financial_entries_status", table_name="group_financial_entries")

        if "ix_group_financial_entries_match_id" in indexes:
            op.drop_index("ix_group_financial_entries_match_id", table_name="group_financial_entries")

        if "ix_group_financial_entries_user_id" in indexes:
            op.drop_index("ix_group_financial_entries_user_id", table_name="group_financial_entries")

        if "ix_group_financial_entries_group_id" in indexes:
            op.drop_index("ix_group_financial_entries_group_id", table_name="group_financial_entries")

        op.drop_table("group_financial_entries")

    group_members_cols = {col["name"] for col in inspector.get_columns("group_members")}
    groups_cols = {col["name"] for col in inspector.get_columns("groups")}

    if "billing_type" in group_members_cols:
        op.drop_column("group_members", "billing_type")

    if "payment_due_day" in groups_cols:
        op.drop_column("groups", "payment_due_day")
