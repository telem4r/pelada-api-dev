"""add queue fields to match_participants

Revision ID: 0034_add_match_participants_queue_fields
Revises: 0033_add_match_join_requests_metadata
Create Date: 2026-03-05

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0034_add_match_participants_queue_fields"
down_revision = "0033_add_match_join_requests_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive-only migration (safe for production)
    with op.batch_alter_table("match_participants") as batch:
        batch.add_column(sa.Column("queue_position", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("waitlist_tier", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # Helpful indexes
    op.create_index(
        "ix_match_participants_match_bucket_queue",
        "match_participants",
        ["match_id", "status", "waitlist_tier", "queue_position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_match_participants_match_bucket_queue", table_name="match_participants")
    with op.batch_alter_table("match_participants") as batch:
        batch.drop_column("requires_approval")
        batch.drop_column("waitlist_tier")
        batch.drop_column("queue_position")
