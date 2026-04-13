"""finance projections and member status

Revision ID: 0050_finance_projections_and_status
Revises: 0049_add_group_single_waitlist_release_days
Create Date: 2026-03-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0050_finance_projections_and_status"
down_revision = "0049_add_group_single_waitlist_release_days"
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


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    table_names = set(inspector.get_table_names())

    groups_cols = {col["name"]: col for col in inspector.get_columns("groups")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}
    fin_entries_cols = {col["name"]: col for col in inspector.get_columns("group_financial_entries")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])
    fin_entry_id_type = _clone_type(fin_entries_cols["id"]["type"])

    if "group_financial_monthly_snapshots" not in table_names:
        op.create_table(
            "group_financial_monthly_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reference_month", sa.Date(), nullable=False),
            sa.Column("total_monthly_fees_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_single_payments_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_fines_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_venue_cost_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_extra_expenses_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("month_result_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("running_cash_balance_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("group_id", "reference_month", name="uq_fin_snapshot_group_month"),
        )

    if "group_member_financial_status" not in table_names:
        op.create_table(
            "group_member_financial_status",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("player_id", player_id_type, sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reference_month", sa.Date(), nullable=False),
            sa.Column("billing_type", sa.String(length=20), nullable=False, server_default="single"),
            sa.Column("monthly_fee_due_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("monthly_fee_paid_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_adimplente", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column(
                "last_payment_entry_id",
                fin_entry_id_type,
                sa.ForeignKey("group_financial_entries.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("group_id", "player_id", "reference_month", name="uq_fin_status_group_player_month"),
        )

    inspector = inspect(bind)

    def ensure_index(table_name, index_name, cols):
        existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if index_name not in existing:
            op.create_index(index_name, table_name, cols)

    ensure_index("group_financial_monthly_snapshots", "ix_fin_snapshot_group_id", ["group_id"])
    ensure_index("group_financial_monthly_snapshots", "ix_fin_snapshot_reference_month", ["reference_month"])

    ensure_index("group_member_financial_status", "ix_fin_status_group_id", ["group_id"])
    ensure_index("group_member_financial_status", "ix_fin_status_player_id", ["player_id"])
    ensure_index("group_member_financial_status", "ix_fin_status_reference_month", ["reference_month"])
    ensure_index("group_member_financial_status", "ix_fin_status_is_adimplente", ["is_adimplente"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    def drop_index_if_exists(table_name, index_name):
        existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if index_name in existing:
            op.drop_index(index_name, table_name=table_name)

    if "group_member_financial_status" in table_names:
        drop_index_if_exists("group_member_financial_status", "ix_fin_status_is_adimplente")
        drop_index_if_exists("group_member_financial_status", "ix_fin_status_reference_month")
        drop_index_if_exists("group_member_financial_status", "ix_fin_status_player_id")
        drop_index_if_exists("group_member_financial_status", "ix_fin_status_group_id")
        op.drop_table("group_member_financial_status")

    if "group_financial_monthly_snapshots" in table_names:
        drop_index_if_exists("group_financial_monthly_snapshots", "ix_fin_snapshot_reference_month")
        drop_index_if_exists("group_financial_monthly_snapshots", "ix_fin_snapshot_group_id")
        op.drop_table("group_financial_monthly_snapshots")
