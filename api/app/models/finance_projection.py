from __future__ import annotations

from .common import *


class GroupFinancialMonthlySnapshot(Base):
    __tablename__ = "group_financial_monthly_snapshots"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    reference_month = Column(Date, nullable=False, index=True)
    total_monthly_fees_cents = Column(Integer, nullable=False, default=0)
    total_single_payments_cents = Column(Integer, nullable=False, default=0)
    total_fines_cents = Column(Integer, nullable=False, default=0)
    total_venue_cost_cents = Column(Integer, nullable=False, default=0)
    total_extra_expenses_cents = Column(Integer, nullable=False, default=0)
    month_result_cents = Column(Integer, nullable=False, default=0)
    running_cash_balance_cents = Column(Integer, nullable=False, default=0)
    generated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("group_id", "reference_month", name="uq_fin_snapshot_group_month"),
    )

    group = relationship("Group")


class GroupMemberFinancialStatus(Base):
    __tablename__ = "group_member_financial_status"

    id = Column(Integer, primary_key=True)
    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    reference_month = Column(Date, nullable=False, index=True)
    billing_type = Column(String(20), nullable=False, default="single")
    monthly_fee_due_cents = Column(Integer, nullable=False, default=0)
    monthly_fee_paid_cents = Column(Integer, nullable=False, default=0)
    is_adimplente = Column(Boolean, nullable=False, default=False, index=True)
    last_payment_entry_id = Column(Integer, ForeignKey("group_financial_entries.id", ondelete="SET NULL"), nullable=True, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("group_id", "player_id", "reference_month", name="uq_fin_status_group_player_month"),
    )

    group = relationship("Group")
    player = relationship("Player")
    last_payment_entry = relationship("GroupFinancialEntry")
