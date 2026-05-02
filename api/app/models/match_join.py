from __future__ import annotations

from .common import *

# =====================================================
# MATCH JOIN REQUEST (jogadores fora do grupo)
# =====================================================

class MatchJoinRequest(Base, TimestampMixin):
    __tablename__ = "match_join_requests"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    # Redundante (facilita queries por grupo e reforça hierarquia /groups/{group_id}/matches/...)
    group_id = Column(String(36), nullable=True, index=True)

    message = Column(Text, nullable=True)

    reviewed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(String(20), nullable=False, default=JoinStatus.pending.value)

    __table_args__ = (
        UniqueConstraint("match_id", "player_id", name="uq_match_join_request_player"),
    )

    match = relationship("Match")
    player = relationship("Player")
