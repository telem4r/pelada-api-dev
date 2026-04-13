from __future__ import annotations

from .common import *

# =====================================================
# PLAYER ACHIEVEMENTS
# =====================================================

class PlayerAchievement(Base, TimestampMixin):
    __tablename__ = "player_achievements"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(80), nullable=False, index=True)
    title = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    unlocked_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    payload = Column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("group_id", "player_id", "code", name="uq_player_achievement_code"),
    )

    group = relationship("Group", back_populates="player_achievements")
    player = relationship("Player", back_populates="achievements")
