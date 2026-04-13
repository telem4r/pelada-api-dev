"""add group_members.player_id and backfill

Revision ID: 0025_add_group_members_player_id
Revises: 0024_fix_groups_fine_columns
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "0025_add_group_members_player_id"
down_revision = "0024_fix_groups_fine_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    gm_columns = {col["name"] for col in inspector.get_columns("group_members")}
    gm_fks = {fk["name"] for fk in inspector.get_foreign_keys("group_members") if fk.get("name")}
    gm_indexes = {idx["name"] for idx in inspector.get_indexes("group_members")}
    gm_uniques = {uc["name"] for uc in inspector.get_unique_constraints("group_members") if uc.get("name")}

    players_columns = {col["name"] for col in inspector.get_columns("players")}

    # 1) adiciona coluna se necessário
    if "player_id" not in gm_columns:
        op.add_column("group_members", sa.Column("player_id", sa.Integer(), nullable=True))
        gm_columns.add("player_id")

    # 2) FK
    if "group_members_player_id_fkey" not in gm_fks:
        op.create_foreign_key(
            "group_members_player_id_fkey",
            "group_members",
            "players",
            ["player_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # 3) índice
    if "ix_group_members_player_id" not in gm_indexes:
        op.create_index("ix_group_members_player_id", "group_members", ["player_id"])

    # 4) backfill apenas se houver colunas compatíveis
    # cenário A: group_members.user_id -> players.owner_id
    if "user_id" in gm_columns and "owner_id" in players_columns:
        bind.execute(text("""
            UPDATE group_members gm
            SET player_id = p.id
            FROM (
                SELECT owner_id, MIN(id) AS id
                FROM players
                GROUP BY owner_id
            ) p
            WHERE gm.user_id = p.owner_id
              AND gm.player_id IS NULL
        """))

    # cenário B: group_members.user_id -> players.user_id
    elif "user_id" in gm_columns and "user_id" in players_columns:
        bind.execute(text("""
            UPDATE group_members gm
            SET player_id = p.id
            FROM (
                SELECT user_id, MIN(id) AS id
                FROM players
                GROUP BY user_id
            ) p
            WHERE gm.user_id = p.user_id
              AND gm.player_id IS NULL
        """))

    # 5) NOT NULL apenas se não houver nulos
    null_count = bind.execute(
        text("SELECT COUNT(*) FROM group_members WHERE player_id IS NULL")
    ).scalar()

    if null_count == 0:
        op.alter_column("group_members", "player_id", nullable=False)

    # 6) unique constraint apenas se ainda não existir
    if "uq_group_member_player" not in gm_uniques:
        op.create_unique_constraint(
            "uq_group_member_player",
            "group_members",
            ["group_id", "player_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    gm_columns = {col["name"] for col in inspector.get_columns("group_members")}
    gm_fks = {fk["name"] for fk in inspector.get_foreign_keys("group_members") if fk.get("name")}
    gm_indexes = {idx["name"] for idx in inspector.get_indexes("group_members")}
    gm_uniques = {uc["name"] for uc in inspector.get_unique_constraints("group_members") if uc.get("name")}

    if "uq_group_member_player" in gm_uniques:
        op.drop_constraint("uq_group_member_player", "group_members", type_="unique")

    if "ix_group_members_player_id" in gm_indexes:
        op.drop_index("ix_group_members_player_id", table_name="group_members")

    if "group_members_player_id_fkey" in gm_fks:
        op.drop_constraint("group_members_player_id_fkey", "group_members", type_="foreignkey")

    if "player_id" in gm_columns:
        op.drop_column("group_members", "player_id")
