from __future__ import annotations

from .common import *

# =====================================================
# MATCH DRAW TEAM
# =====================================================

class MatchDrawTeam(Base):
    __tablename__ = "match_draw_teams"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    team_number = Column(Integer, nullable=False)

    players = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("match_id", "team_number", name="uq_match_team_number"),
    )

    match = relationship("Match", back_populates="draw_teams")
