"""add period fields and unique constraints for finance automation

Revision ID: 0028_add_fin_entry_period_fields
Revises: 0027_add_group_financial_entries
Create Date: 2026-03-03

"""

from alembic import op
import sqlalchemy as sa


revision = "0028_add_fin_entry_period_fields"
down_revision = "0027_add_group_financial_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("group_financial_entries", sa.Column("period_year", sa.Integer(), nullable=True))
    op.add_column("group_financial_entries", sa.Column("period_month", sa.Integer(), nullable=True))

    op.create_index("ix_group_financial_entries_period_year", "group_financial_entries", ["period_year"])
    op.create_index("ix_group_financial_entries_period_month", "group_financial_entries", ["period_month"])

    # 1 mensalidade por user por mês
    op.create_unique_constraint(
        "uq_fin_entry_monthly_user_period",
        "group_financial_entries",
        ["group_id", "user_id", "entry_type", "period_year", "period_month"],
    )

    # 1 cobrança single/fine por user por partida (quando match_id existe)
    op.create_unique_constraint(
        "uq_fin_entry_user_match_type",
        "group_financial_entries",
        ["group_id", "user_id", "entry_type", "match_id"],
    )

    # 1 despesa por partida (user_id NULL)
    op.create_unique_constraint(
        "uq_fin_entry_group_match_type",
        "group_financial_entries",
        ["group_id", "entry_type", "match_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_fin_entry_group_match_type", "group_financial_entries", type_="unique")
    op.drop_constraint("uq_fin_entry_user_match_type", "group_financial_entries", type_="unique")
    op.drop_constraint("uq_fin_entry_monthly_user_period", "group_financial_entries", type_="unique")

    op.drop_index("ix_group_financial_entries_period_month", table_name="group_financial_entries")
    op.drop_index("ix_group_financial_entries_period_year", table_name="group_financial_entries")

    op.drop_column("group_financial_entries", "period_month")
    op.drop_column("group_financial_entries", "period_year")
