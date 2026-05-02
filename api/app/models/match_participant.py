from __future__ import annotations

from .common import *

# =====================================================
# MATCH PARTICIPANT
# =====================================================

class MatchParticipant(Base, TimestampMixin):
    __tablename__ = "match_participants"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String(20), nullable=False, default=ParticipantStatus.confirmed.value)
    arrived = Column(Boolean, nullable=False, default=False)
    paid = Column(Boolean, nullable=False, default=False)
    no_show = Column(Boolean, nullable=False, default=False)
    no_show_justified = Column(Boolean, nullable=False, default=False)
    no_show_reason = Column(Text, nullable=True)

    # ✅ Fase 2: ordenação e regras de lista
    # - queue_position: ordem de chegada por match (principalmente na waitlist)
    # - waitlist_tier: 0 = normal, 1 = fim (inadimplentes)
    # - requires_approval: se True, não pode ser promovido automaticamente
    queue_position = Column(Integer, nullable=True)
    waitlist_tier = Column(Integer, nullable=False, default=0)
    requires_approval = Column(Boolean, nullable=False, default=False)
    position = Column(String(20), nullable=True)

    __table_args__ = (
        UniqueConstraint("match_id", "player_id", name="uq_match_participant"),
    )

    match = relationship("Match", back_populates="participants")
    player = relationship("Player", back_populates="match_participations")
