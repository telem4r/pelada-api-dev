from __future__ import annotations

from .common import *

# =====================================================
# PLAYER
# =====================================================

class Player(Base, TimestampMixin):
    __tablename__ = "players"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)

    display_name = Column(String(120), nullable=False)
    full_name = Column(String(200), nullable=True)
    nickname = Column(String(80), nullable=True)

    team_id = Column(UUID(as_uuid=False), ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)

    primary_position = Column(String(80), nullable=True)
    secondary_position = Column(String(80), nullable=True)
    preferred_foot = Column(String(20), nullable=True)
    avatar_url = Column(String(500), nullable=True)
    rating = Column(Integer, nullable=False, default=0)
    is_public = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)

    owner = relationship("User", back_populates="players")
    team = relationship("Team", back_populates="players")
    group_memberships = relationship("GroupMember", back_populates="player")
