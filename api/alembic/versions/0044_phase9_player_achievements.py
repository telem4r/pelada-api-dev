"""phase9 player achievements

Revision ID: 0044_phase9_player_achievements
Revises: 0043_phase8_finance_advanced
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "0044_phase9_player_achievements"
down_revision = "0043_phase8_finance_advanced"
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
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    if "player_achievements" not in table_names:
        op.create_table(
            "player_achievements",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("player_id", player_id_type, nullable=False),
            sa.Column("code", sa.String(length=80), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "unlocked_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("group_id", "player_id", "code", name="uq_player_achievement_code"),
        )

    inspector = inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("player_achievements")}

    if "ix_player_achievements_group_id" not in indexes:
        op.create_index("ix_player_achievements_group_id", "player_achievements", ["group_id"], unique=False)

    if "ix_player_achievements_player_id" not in indexes:
        op.create_index("ix_player_achievements_player_id", "player_achievements", ["player_id"], unique=False)

    if "ix_player_achievements_code" not in indexes:
        op.create_index("ix_player_achievements_code", "player_achievements", ["code"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    table_names = set(inspector.get_table_names())
    if "player_achievements" not in table_names:
        return

    indexes = {idx["name"] for idx in inspector.get_indexes("player_achievements")}

    if "ix_player_achievements_code" in indexes:
        op.drop_index("ix_player_achievements_code", table_name="player_achievements")

    if "ix_player_achievements_player_id" in indexes:
        op.drop_index("ix_player_achievements_player_id", table_name="player_achievements")

    if "ix_player_achievements_group_id" in indexes:
        op.drop_index("ix_player_achievements_group_id", table_name="player_achievements")

    op.drop_table("player_achievements")
