"""phase11 social feed and ratings

Revision ID: 0051_phase11_social_feed_ratings
Revises: 0050_finance_projections_and_status
Create Date: 2026-03-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0051_phase11_social_feed_ratings"
down_revision = "0050_finance_projections_and_status"
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
    matches_cols = {col["name"]: col for col in inspector.get_columns("matches")}
    players_cols = {col["name"]: col for col in inspector.get_columns("players")}

    group_id_type = _clone_type(groups_cols["id"]["type"])
    match_id_type = _clone_type(matches_cols["id"]["type"])
    player_id_type = _clone_type(players_cols["id"]["type"])

    if "player_ratings" not in table_names:
        op.create_table(
            "player_ratings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("match_id", match_id_type, nullable=False),
            sa.Column("rater_player_id", player_id_type, nullable=False),
            sa.Column("rated_player_id", player_id_type, nullable=False),
            sa.Column("skill", sa.Integer(), nullable=False),
            sa.Column("fair_play", sa.Integer(), nullable=False),
            sa.Column("commitment", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["rater_player_id"], ["players.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["rated_player_id"], ["players.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("match_id", "rater_player_id", "rated_player_id", name="uq_player_rating_once_per_match"),
        )

    if "group_ratings" not in table_names:
        op.create_table(
            "group_ratings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("group_id", group_id_type, nullable=False),
            sa.Column("player_id", player_id_type, nullable=False),
            sa.Column("organization", sa.Integer(), nullable=False),
            sa.Column("fair_play", sa.Integer(), nullable=False),
            sa.Column("level", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("group_id", "player_id", name="uq_group_rating_once_per_player"),
        )

    if "social_posts" not in table_names:
        op.create_table(
            "social_posts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("player_id", player_id_type, nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        )

    if "social_post_comments" not in table_names:
        op.create_table(
            "social_post_comments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("post_id", sa.Integer(), nullable=False),
            sa.Column("player_id", player_id_type, nullable=False),
            sa.Column("comment", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["post_id"], ["social_posts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
        )

    if "social_post_likes" not in table_names:
        op.create_table(
            "social_post_likes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("post_id", sa.Integer(), nullable=False),
            sa.Column("player_id", player_id_type, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.ForeignKeyConstraint(["post_id"], ["social_posts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["player_id"], ["players.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("post_id", "player_id", name="uq_social_post_like_once"),
        )

    inspector = inspect(bind)

    def ensure_index(table_name, index_name, cols, unique=False):
        existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if index_name not in existing:
            op.create_index(index_name, table_name, cols, unique=unique)

    ensure_index("player_ratings", "ix_player_ratings_match_id", ["match_id"])
    ensure_index("player_ratings", "ix_player_ratings_rater_player_id", ["rater_player_id"])
    ensure_index("player_ratings", "ix_player_ratings_rated_player_id", ["rated_player_id"])

    ensure_index("group_ratings", "ix_group_ratings_group_id", ["group_id"])
    ensure_index("group_ratings", "ix_group_ratings_player_id", ["player_id"])

    ensure_index("social_posts", "ix_social_posts_player_id", ["player_id"])

    ensure_index("social_post_comments", "ix_social_post_comments_post_id", ["post_id"])
    ensure_index("social_post_comments", "ix_social_post_comments_player_id", ["player_id"])

    ensure_index("social_post_likes", "ix_social_post_likes_post_id", ["post_id"])
    ensure_index("social_post_likes", "ix_social_post_likes_player_id", ["player_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    def drop_index_if_exists(table_name, index_name):
        existing = {idx["name"] for idx in inspector.get_indexes(table_name)}
        if index_name in existing:
            op.drop_index(index_name, table_name=table_name)

    if "social_post_likes" in table_names:
        drop_index_if_exists("social_post_likes", "ix_social_post_likes_player_id")
        drop_index_if_exists("social_post_likes", "ix_social_post_likes_post_id")
        op.drop_table("social_post_likes")

    if "social_post_comments" in table_names:
        drop_index_if_exists("social_post_comments", "ix_social_post_comments_player_id")
        drop_index_if_exists("social_post_comments", "ix_social_post_comments_post_id")
        op.drop_table("social_post_comments")

    if "social_posts" in table_names:
        drop_index_if_exists("social_posts", "ix_social_posts_player_id")
        op.drop_table("social_posts")

    if "group_ratings" in table_names:
        drop_index_if_exists("group_ratings", "ix_group_ratings_player_id")
        drop_index_if_exists("group_ratings", "ix_group_ratings_group_id")
        op.drop_table("group_ratings")

    if "player_ratings" in table_names:
        drop_index_if_exists("player_ratings", "ix_player_ratings_rated_player_id")
        drop_index_if_exists("player_ratings", "ix_player_ratings_rater_player_id")
        drop_index_if_exists("player_ratings", "ix_player_ratings_match_id")
        op.drop_table("player_ratings")
