"""add missing payments.owner_id if absent

Revision ID: 0047_add_missing_payments_owner_id
Revises: 0046_phase11_social_and_geo
Create Date: 2026-03-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0047_add_missing_payments_owner_id"
down_revision = "0046_phase11_social_and_geo"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_column("payments", "owner_id"):
        op.add_column("payments", sa.Column("owner_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "fk_payments_owner_id_users",
            "payments",
            "users",
            ["owner_id"],
            ["id"],
            ondelete="CASCADE",
        )

        op.execute(
            sa.text(
                """
                UPDATE payments p
                SET owner_id = gm.user_id
                FROM group_members gm
                WHERE gm.group_id = p.group_id
                  AND gm.role = 'owner'
                  AND p.owner_id IS NULL
                """
            )
        )

        op.execute(sa.text("UPDATE payments SET owner_id = 1 WHERE owner_id IS NULL"))
        op.alter_column("payments", "owner_id", nullable=False)

    if not _has_index("payments", "ix_payments_owner_id"):
        op.create_index("ix_payments_owner_id", "payments", ["owner_id"], unique=False)


def downgrade() -> None:
    # downgrade conservador para evitar perda de dados em produção
    pass
