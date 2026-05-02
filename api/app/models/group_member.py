from __future__ import annotations

from .common import *

# =====================================================
# GROUP MEMBER
# =====================================================

class GroupMember(Base, TimestampMixin):
    __tablename__ = "group_members"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)

    group_id = Column(UUID(as_uuid=False), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(UUID(as_uuid=False), ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)

    role = Column(String(20), nullable=False, default=GroupRole.member.value)
    status = Column(String(20), nullable=False, default=MembershipStatus.pending.value)
    billing_type = Column(String(20), nullable=False, default="avulso")
    skill_rating = Column(Integer, nullable=False, default=3)
    joined_at = Column(DateTime(timezone=True), nullable=True, default=utcnow)

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_member_user"),
        UniqueConstraint("group_id", "player_id", name="uq_group_member_player"),
    )

    group = relationship("Group", back_populates="members")
    user = relationship("User", back_populates="group_memberships")
    player = relationship("Player", back_populates="group_memberships")
