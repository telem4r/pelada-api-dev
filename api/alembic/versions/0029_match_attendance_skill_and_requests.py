"""match attendance: guests, join requests, skill rating, no-show fields

Revision ID: 0029_match_attendance_skill_and_requests
Revises: 0028_add_fin_entry_period_fields
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "0029_match_attendance_skill_and_requests"
down_revision = "0028_add_fin_entry_period_fields"
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
    matches_cols = {col["name"]: col for col in inspector.get_columns("matches")}
    users_cols = {col["name"]: col for col in inspector.get_columns("users")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    match_id_type = _clone_type(matches_cols["id"]["type"])
    user_id_type = _clone_type(users_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    # 1) match_join_requests
    if "match_join_requests" not in table_names:
        op.create_table(
            "match_join_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("match_id", match_id_type, sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", user_id_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("player_id", player_id_type, sa.ForeignKey("players.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("match_id", "player_id", name="uq_match_join_request_player"),
        )

    # 2) match_guests
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "match_guests" not in table_names:
        op.create_table(
            "match_guests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("match_id", match_id_type, sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
            sa.Column("group_id", group_id_type, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("position", sa.String(length=50), nullable=True),
            sa.Column("skill_rating", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="confirmed"),
            sa.Column("arrived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("no_show", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("no_show_justified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("no_show_reason", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

    # 3) matches.single_waitlist_release_days
    matches_current_cols = {col["name"] for col in inspector.get_columns("matches")}
    if "single_waitlist_release_days" not in matches_current_cols:
        op.add_column(
            "matches",
            sa.Column("single_waitlist_release_days", sa.Integer(), nullable=False, server_default="0"),
        )

    # 4) match_participants fields
    mp_cols = {col["name"] for col in inspector.get_columns("match_participants")}

    if "arrived" not in mp_cols:
        op.add_column(
            "match_participants",
            sa.Column("arrived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    if "no_show" not in mp_cols:
        op.add_column(
            "match_participants",
            sa.Column("no_show", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    if "no_show_justified" not in mp_cols:
        op.add_column(
            "match_participants",
            sa.Column("no_show_justified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )

    if "no_show_reason" not in mp_cols:
        op.add_column(
            "match_participants",
            sa.Column("no_show_reason", sa.String(length=255), nullable=True),
        )

    # 5) group_members.skill_rating
    gm_cols = {col["name"] for col in inspector.get_columns("group_members")}
    if "skill_rating" not in gm_cols:
        op.add_column(
            "group_members",
            sa.Column("skill_rating", sa.Integer(), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)

    table_names = set(inspector.get_table_names())

    gm_cols = {col["name"] for col in inspector.get_columns("group_members")}
    if "skill_rating" in gm_cols:
        op.drop_column("group_members", "skill_rating")

    mp_cols = {col["name"] for col in inspector.get_columns("match_participants")}
    for col_name in ["no_show_reason", "no_show_justified", "no_show", "arrived"]:
        if col_name in mp_cols:
            op.drop_column("match_participants", col_name)

    matches_cols = {col["name"] for col in inspector.get_columns("matches")}
    if "single_waitlist_release_days" in matches_cols:
        op.drop_column("matches", "single_waitlist_release_days")

    if "match_guests" in table_names:
        op.drop_table("match_guests")

    if "match_join_requests" in table_names:
        op.drop_table("match_join_requests")
