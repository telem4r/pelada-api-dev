"""add group creation fields

Revision ID: 0005_group_creation_fields
Revises: 0004_groups_finance
Create Date: 2026-02-21

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0005_group_creation_fields"
down_revision = "0004_groups_finance"
branch_labels = None
depends_on = None


def upgrade():
    # Mantemos nullable=True para não quebrar dados existentes.
    op.add_column("groups", sa.Column("country", sa.String(length=80), nullable=True))
    op.add_column("groups", sa.Column("state", sa.String(length=80), nullable=True))
    op.add_column("groups", sa.Column("city", sa.String(length=120), nullable=True))
    op.add_column("groups", sa.Column("modality", sa.String(length=40), nullable=True))
    op.add_column("groups", sa.Column("group_type", sa.String(length=20), nullable=True))
    op.add_column("groups", sa.Column("gender_type", sa.String(length=20), nullable=True))
    op.add_column("groups", sa.Column("payment_method", sa.String(length=20), nullable=True))


def downgrade():
    op.drop_column("groups", "payment_method")
    op.drop_column("groups", "gender_type")
    op.drop_column("groups", "group_type")
    op.drop_column("groups", "modality")
    op.drop_column("groups", "city")
    op.drop_column("groups", "state")
    op.drop_column("groups", "country")
