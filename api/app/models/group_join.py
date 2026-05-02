from __future__ import annotations

from .common import *

# =====================================================
# GROUP JOIN REQUEST
# =====================================================

class GroupJoinRequest(Base, TimestampMixin):
    __tablename__ = "group_join_requests"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)

    group_id = Column(UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    status = Column(String(20), nullable=False, default=MembershipStatus.pending.value)
    message = Column(Text, nullable=True)

    reviewed_by_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("group_id", "player_id", name="uq_group_join_req_group_player"),
    )

    group = relationship("Group")
    player = relationship("Player")
