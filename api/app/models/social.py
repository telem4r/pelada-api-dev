from __future__ import annotations

from .common import *

# =====================================================
# FASE 11 - SOCIAL / GEOLOCATION
# =====================================================

class PlayerProfile(Base, TimestampMixin):
    __tablename__ = "player_profiles"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    bio = Column(Text, nullable=True)
    city = Column(String(120), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    main_position = Column(String(80), nullable=True)
    skill_level = Column(Integer, nullable=False, default=3)

    player = relationship("Player", back_populates="sports_profile")


class PlayerNetwork(Base, TimestampMixin):
    __tablename__ = "player_network"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    connected_player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    shared_matches_count = Column(Integer, nullable=False, default=0)
    invited_count = Column(Integer, nullable=False, default=0)
    last_played_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("player_id", "connected_player_id", name="uq_player_network_pair"),
    )

    player = relationship("Player", foreign_keys=[player_id])
    connected_player = relationship("Player", foreign_keys=[connected_player_id])


class Friendship(Base, TimestampMixin):
    __tablename__ = "friendships"

    id = Column(Integer, primary_key=True)
    requester_player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    addressee_player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("requester_player_id", "addressee_player_id", name="uq_friendship_request_pair"),
    )

    requester = relationship("Player", foreign_keys=[requester_player_id])
    addressee = relationship("Player", foreign_keys=[addressee_player_id])


class SocialFeedEvent(Base, TimestampMixin):
    __tablename__ = "social_feed_events"

    id = Column(Integer, primary_key=True)
    event_type = Column(String(40), nullable=False, index=True)
    actor_player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    target_player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)
    metadata_json = Column("metadata", JSONB, nullable=True)

    actor_player = relationship("Player", foreign_keys=[actor_player_id])
    target_player = relationship("Player", foreign_keys=[target_player_id])
    group = relationship("Group")
    match = relationship("Match")


class PlayerRating(Base, TimestampMixin):
    __tablename__ = "player_ratings"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=True, index=True)
    rater_player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    rated_player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    skill = Column(Integer, nullable=False)
    fair_play = Column(Integer, nullable=False)
    commitment = Column(Integer, nullable=False)
    review_origin = Column(String(40), nullable=False, default="group_member_manual", index=True)

    __table_args__ = (
        UniqueConstraint("match_id", "rater_player_id", "rated_player_id", name="uq_player_rating_once_per_match"),
    )

    group = relationship("Group")
    match = relationship("Match")
    rater = relationship("Player", foreign_keys=[rater_player_id])
    rated = relationship("Player", foreign_keys=[rated_player_id])


class GroupRating(Base, TimestampMixin):
    __tablename__ = "group_ratings"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    organization = Column(Integer, nullable=False)
    fair_play = Column(Integer, nullable=False)
    level = Column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("group_id", "player_id", name="uq_group_rating_once_per_player"),
    )

    group = relationship("Group")
    player = relationship("Player")


class SocialPost(Base, TimestampMixin):
    __tablename__ = "social_posts"

    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)

    player = relationship("Player")
    comments = relationship("SocialPostComment", back_populates="post", cascade="all, delete-orphan")
    likes = relationship("SocialPostLike", back_populates="post", cascade="all, delete-orphan")


class SocialPostComment(Base, TimestampMixin):
    __tablename__ = "social_post_comments"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    comment = Column(Text, nullable=False)

    post = relationship("SocialPost", back_populates="comments")
    player = relationship("Player")


class SocialPostLike(Base, TimestampMixin):
    __tablename__ = "social_post_likes"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("post_id", "player_id", name="uq_social_post_like_once"),
    )

    post = relationship("SocialPost", back_populates="likes")
    player = relationship("Player")
