from __future__ import annotations

from .common import *

# =====================================================
# TEAM
# =====================================================

class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    owner_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)

    owner = relationship("User", back_populates="teams")
    players = relationship("Player", back_populates="team")
