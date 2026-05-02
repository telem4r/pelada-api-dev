from __future__ import annotations

from .common import *

# =====================================================
# MATCH GUEST PLAYER (sem app)
# =====================================================

class MatchGuestPlayer(Base, TimestampMixin):
    __tablename__ = "match_guests"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(120), nullable=False)
    position = Column(String(50), nullable=True)
    skill_rating = Column(Integer, nullable=False, default=3)

    status = Column(String(20), nullable=False, default=ParticipantStatus.confirmed.value)
    arrived = Column(Boolean, nullable=False, default=False)

    no_show = Column(Boolean, nullable=False, default=False)
    no_show_justified = Column(Boolean, nullable=False, default=False)
    no_show_reason = Column(Text, nullable=True)

    match = relationship(
        "Match",
        foreign_keys=[match_id],
        back_populates="guests",
    )
