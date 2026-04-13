from __future__ import annotations

from .common import *

# =====================================================
# PAYMENT
# =====================================================

class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)

    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # ✅ groups.id agora é string uuid
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)

    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)

    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="BRL")
    status = Column(String(20), nullable=False, default="pending")

    kind = Column(String(30), nullable=False, default="group")
    description = Column(Text, nullable=True)

    paid = Column(Boolean, nullable=False, default=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)

    confirmed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    group = relationship("Group", back_populates="payments")
    match = relationship("Match", back_populates="payments")

    owner = relationship("User", foreign_keys=[owner_id], back_populates="payments_owned")
    player = relationship("Player", back_populates="payments")
    confirmed_by = relationship("User", foreign_keys=[confirmed_by_user_id], back_populates="payments_confirmed")
