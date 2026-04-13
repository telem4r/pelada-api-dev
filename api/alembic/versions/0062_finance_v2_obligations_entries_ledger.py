"""finance v2 obligations entries ledger

Revision ID: 0062_finance_v2_obligations_entries_ledger
Revises: 0061_matches_v2_stats_and_richer_events
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0062_finance_v2_obligations_entries_ledger"
down_revision = "0061_matches_v2_stats_and_richer_events"
branch_labels = None
depends_on = None


def _clone_type(col_type):
    if isinstance(col_type, sa.Integer):
        return sa.Integer()
    if isinstance(col_type, sa.BigInteger):
        return sa.BigInteger()
    if isinstance(col_type, sa.String):
        return sa.String(length=col_type.length)
    if isinstance(col_type, sa.Uuid):
        return sa.Uuid()
    return col_type.__class__()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names(schema="public")) | set(inspector.get_table_names())

    groups_cols = {col["name"]: col for col in inspector.get_columns("groups")}
    users_cols = {col["name"]: col for col in inspector.get_columns("users")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    user_id_type = _clone_type(users_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    if "finance_obligations_v2" not in table_names:
        op.create_table(
            "finance_obligations_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("user_id", user_id_type, nullable=True),
            sa.Column("player_id", player_id_type, nullable=True),
            sa.Column("match_id", sa.Uuid(), nullable=True),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="BRL"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="aberta"),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("competence_month", sa.Integer(), nullable=True),
            sa.Column("competence_year", sa.Integer(), nullable=True),
            sa.Column("created_by_user_id", user_id_type, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["match_id"], ["public.matches_v2.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
            sa.CheckConstraint("amount > 0", name="ck_fin_obligations_v2_amount_positive"),
            sa.CheckConstraint("status in ('aberta', 'parcial', 'paga', 'cancelada')", name="ck_fin_obligations_v2_status"),
            schema="public",
        )

    if "finance_entries_v2" not in table_names:
        op.create_table(
            "finance_entries_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("obligation_id", sa.Uuid(), nullable=True),
            sa.Column("user_id", user_id_type, nullable=True),
            sa.Column("player_id", player_id_type, nullable=True),
            sa.Column("match_id", sa.Uuid(), nullable=True),
            sa.Column("entry_type", sa.String(length=20), nullable=False),
            sa.Column("category", sa.String(length=50), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="BRL"),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", user_id_type, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["obligation_id"], ["public.finance_obligations_v2.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["match_id"], ["public.matches_v2.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
            sa.CheckConstraint("entry_type in ('inflow', 'outflow')", name="ck_fin_entries_v2_type"),
            sa.CheckConstraint("amount > 0", name="ck_fin_entries_v2_amount_positive"),
            schema="public",
        )

    if "finance_ledger_v2" not in table_names:
        op.create_table(
            "finance_ledger_v2",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("obligation_id", sa.Uuid(), nullable=True),
            sa.Column("entry_id", sa.Uuid(), nullable=True),
            sa.Column("movement_type", sa.String(length=40), nullable=False),
            sa.Column("direction", sa.String(length=20), nullable=False),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False),
            sa.Column("balance_impact", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("description", sa.String(length=255), nullable=False),
            sa.Column("reference_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["obligation_id"], ["public.finance_obligations_v2.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["entry_id"], ["public.finance_entries_v2.id"], ondelete="SET NULL"),
            sa.CheckConstraint("direction in ('inflow', 'outflow')", name="ck_fin_ledger_v2_direction"),
            schema="public",
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_fin_obligations_v2_group_status ON public.finance_obligations_v2(group_id, status, due_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fin_entries_v2_group_paid_at ON public.finance_entries_v2(group_id, paid_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fin_ledger_v2_group_reference_date ON public.finance_ledger_v2(group_id, reference_date DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_fin_ledger_v2_group_reference_date")
    op.execute("DROP INDEX IF EXISTS ix_fin_entries_v2_group_paid_at")
    op.execute("DROP INDEX IF EXISTS ix_fin_obligations_v2_group_status")
    op.execute("DROP TABLE IF EXISTS public.finance_ledger_v2")
    op.execute("DROP TABLE IF EXISTS public.finance_entries_v2")
    op.execute("DROP TABLE IF EXISTS public.finance_obligations_v2")
