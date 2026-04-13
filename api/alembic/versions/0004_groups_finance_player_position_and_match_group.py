"""groups + members + finance + match.group_id + player.position

Revision ID: 0004_groups_finance
Revises: 0003_presence_draw_rating
Create Date: 2026-02-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_groups_finance"
down_revision = "0003_presence_draw_rating"
branch_labels = None
depends_on = None


def upgrade():
    """Migração idempotente.
    Motivo: alguns ambientes já podem ter tabelas criadas parcialmente.
    """

    conn = op.get_bind()

    def _table_exists(table: str) -> bool:
        q = sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:t
            )
            """
        )
        return bool(conn.execute(q, {"t": table}).scalar())

    def _column_exists(table: str, column: str) -> bool:
        q = sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=:t AND column_name=:c
            )
            """
        )
        return bool(conn.execute(q, {"t": table, "c": column}).scalar())

    def _index_exists(index_name: str) -> bool:
        q = sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_indexes
                WHERE schemaname='public' AND indexname=:i
            )
            """
        )
        return bool(conn.execute(q, {"i": index_name}).scalar())

    # players: add position (compat com players_routes.py)
    if _table_exists("players") and not _column_exists("players", "position"):
        op.add_column("players", sa.Column("position", sa.String(length=80), nullable=False, server_default=""))
        op.alter_column("players", "position", server_default=None)

    # groups
    if not _table_exists("groups"):
        op.create_table(
            "groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="BRL"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
    else:
        # tabela já existe: garantir colunas mínimas usadas pelo app
        if not _column_exists("groups", "owner_id"):
            op.add_column("groups", sa.Column("owner_id", sa.Integer(), nullable=True))
        if not _column_exists("groups", "name"):
            op.add_column("groups", sa.Column("name", sa.String(length=120), nullable=True))
        if not _column_exists("groups", "currency"):
            op.add_column("groups", sa.Column("currency", sa.String(length=8), nullable=False, server_default="BRL"))
            op.alter_column("groups", "currency", server_default=None)
        if not _column_exists("groups", "created_at"):
            op.add_column(
                "groups",
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            )
            op.alter_column("groups", "created_at", server_default=None)
        if not _column_exists("groups", "updated_at"):
            op.add_column(
                "groups",
                sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            )
            op.alter_column("groups", "updated_at", server_default=None)

        # FK owner_id -> users.id (tenta criar; se já existir, ignora)
        try:
            op.create_foreign_key(
                "fk_groups_owner_id",
                "groups",
                "users",
                ["owner_id"],
                ["id"],
                ondelete="CASCADE",
            )
        except Exception:
            pass

    if not _index_exists("ix_groups_id"):
        try:
            op.create_index("ix_groups_id", "groups", ["id"])
        except Exception:
            pass
    if _column_exists("groups", "owner_id") and not _index_exists("ix_groups_owner_id"):
        try:
            op.create_index("ix_groups_owner_id", "groups", ["owner_id"])
        except Exception:
            pass

    # group_members
    if not _table_exists("group_members"):
        op.create_table(
            "group_members",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
            sa.Column("member_type", sa.String(length=20), nullable=False, server_default="avulso"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("rating", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("group_id", "player_id", name="uq_group_member"),
        )
        op.create_index("ix_group_members_id", "group_members", ["id"])
        op.create_index("ix_group_members_group_id", "group_members", ["group_id"])
        op.create_index("ix_group_members_player_id", "group_members", ["player_id"])

    # matches: add group_id + make teams nullable (draw-first)
    if _table_exists("matches") and not _column_exists("matches", "group_id"):
        op.add_column("matches", sa.Column("group_id", sa.Integer(), nullable=True))
        try:
            op.create_index("ix_matches_group_id", "matches", ["group_id"])
        except Exception:
            pass
        try:
            op.create_foreign_key("fk_matches_group_id", "matches", "groups", ["group_id"], ["id"], ondelete="CASCADE")
        except Exception:
            pass

    # Tornar times nullable (se já estiver, ignora)
    try:
        op.alter_column("matches", "home_team_id", existing_type=sa.Integer(), nullable=True)
    except Exception:
        pass
    try:
        op.alter_column("matches", "away_team_id", existing_type=sa.Integer(), nullable=True)
    except Exception:
        pass

    # payments
    if not _table_exists("payments"):
        op.create_table(
            "payments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
            sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="SET NULL"), nullable=True),
            sa.Column("amount_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="BRL"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )
        op.create_index("ix_payments_id", "payments", ["id"])
        op.create_index("ix_payments_owner_id", "payments", ["owner_id"])
        op.create_index("ix_payments_group_id", "payments", ["group_id"])
        op.create_index("ix_payments_player_id", "payments", ["player_id"])
        op.create_index("ix_payments_match_id", "payments", ["match_id"])
        op.create_index("ix_payments_confirmed_by_user_id", "payments", ["confirmed_by_user_id"])


def downgrade():
    op.drop_index("ix_payments_confirmed_by_user_id", table_name="payments")
    op.drop_index("ix_payments_match_id", table_name="payments")
    op.drop_index("ix_payments_player_id", table_name="payments")
    op.drop_index("ix_payments_group_id", table_name="payments")
    op.drop_index("ix_payments_owner_id", table_name="payments")
    op.drop_index("ix_payments_id", table_name="payments")
    op.drop_table("payments")

    try:
        op.drop_constraint("fk_matches_group_id", "matches", type_="foreignkey")
    except Exception:
        pass
    try:
        op.drop_index("ix_matches_group_id", table_name="matches")
    except Exception:
        pass
    try:
        op.drop_column("matches", "group_id")
    except Exception:
        pass

    try:
        op.drop_index("ix_group_members_player_id", table_name="group_members")
        op.drop_index("ix_group_members_group_id", table_name="group_members")
        op.drop_index("ix_group_members_id", table_name="group_members")
        op.drop_table("group_members")
    except Exception:
        pass

    try:
        op.drop_index("ix_groups_owner_id", table_name="groups")
        op.drop_index("ix_groups_id", table_name="groups")
        op.drop_table("groups")
    except Exception:
        pass

    try:
        op.drop_column("players", "position")
    except Exception:
        pass
