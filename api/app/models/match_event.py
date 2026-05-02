from __future__ import annotations

from .common import *

# =====================================================
# MATCH EVENT
# =====================================================

class MatchEvent(Base, TimestampMixin):
    __tablename__ = "match_events"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    team_number = Column(Integer, nullable=False)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True, index=True)
    guest_id = Column(Integer, ForeignKey("match_guests.id", ondelete="SET NULL"), nullable=True, index=True)
    event_type = Column(String(30), nullable=False, default="goal")
    minute = Column(Integer, nullable=True)

    match = relationship("Match", back_populates="events")
    player = relationship("Player")
    guest = relationship("MatchGuestPlayer")
