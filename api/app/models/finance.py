from __future__ import annotations

from .common import *

# =====================================================
# GROUP FINANCIAL ENTRIES (controle interno)
# =====================================================


class GroupFinancialEntry(Base, TimestampMixin):
    __tablename__ = "group_financial_entries"

    id = Column(Integer, primary_key=True)

    group_id = Column(String(36), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    match_id = Column(Integer, ForeignKey("matches.id", ondelete="SET NULL"), nullable=True, index=True)

    # para lançamentos automáticos (mensalidade)
    period_year = Column(Integer, nullable=True, index=True)
    period_month = Column(Integer, nullable=True, index=True)

    # monthly | single | fine | manual | venue
    entry_type = Column(String(20), nullable=False, default="manual")

    amount_cents = Column(Integer, nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="BRL")

    # pending | paid
    status = Column(String(20), nullable=False, default="pending")
    due_date = Column(Date, nullable=True)

    description = Column(Text, nullable=True)

    paid = Column(Boolean, nullable=False, default=False)

    # Falta (no-show) - marcado pelo ADM/Owner
    no_show = Column(Boolean, nullable=False, default=False)
    no_show_justified = Column(Boolean, nullable=False, default=False)
    no_show_reason = Column(Text, nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    paid_amount_cents = Column(Integer, nullable=False, default=0)
    payment_method = Column(String(30), nullable=True)
    notes = Column(Text, nullable=True)
    confirmed_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    __table_args__ = (
        # 1 mensalidade por user por mês
        UniqueConstraint(
            "group_id",
            "user_id",
            "entry_type",
            "period_year",
            "period_month",
            name="uq_fin_entry_monthly_user_period",
        ),
        # 1 cobrança single/fine por user por partida (quando match_id existe)
        UniqueConstraint(
            "group_id",
            "user_id",
            "entry_type",
            "match_id",
            name="uq_fin_entry_user_match_type",
        ),
        # 1 despesa de quadra por partida (user_id NULL)
        UniqueConstraint(
            "group_id",
            "entry_type",
            "match_id",
            name="uq_fin_entry_group_match_type",
        ),
    )

    group = relationship("Group")
    user = relationship("User", foreign_keys=[user_id])
    match = relationship("Match")
    confirmed_by = relationship("User", foreign_keys=[confirmed_by_user_id])
