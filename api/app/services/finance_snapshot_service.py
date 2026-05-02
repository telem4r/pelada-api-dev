from __future__ import annotations

from datetime import date
from sqlalchemy.orm import Session

from app.models import GroupFinancialEntry, GroupFinancialMonthlySnapshot
from app.repositories.finance import get_snapshot
from app.services.finance_summary_service import build_month_rollup


def rebuild_snapshot(db: Session, *, group_id: str, reference_month: date) -> GroupFinancialMonthlySnapshot:
    rollup = build_month_rollup(db, group_id=group_id, reference_month=reference_month)
    snapshot = get_snapshot(db, group_id, reference_month)
    if snapshot is None:
        snapshot = GroupFinancialMonthlySnapshot(group_id=group_id, reference_month=reference_month)
    t = rollup["totals"]
    snapshot.total_monthly_fees_cents = int(t.get("monthly", 0))
    snapshot.total_single_payments_cents = int(t.get("single", 0))
    snapshot.total_fines_cents = int(t.get("fine", 0))
    snapshot.total_venue_cost_cents = int(t.get("venue", 0))
    snapshot.total_extra_expenses_cents = int(t.get("extra_expense", 0))
    snapshot.month_result_cents = int(rollup["month_result_cents"])
    # running balance = all paid entries to date (simple deterministic rebuild)
    paid_entries = db.query(GroupFinancialEntry).filter(GroupFinancialEntry.group_id == group_id, GroupFinancialEntry.status == "paid").all()
    balance = 0
    for row in paid_entries:
        amount = abs(int(getattr(row, "paid_amount_cents", None) or row.amount_cents or 0))
        if (row.entry_type or "").lower() in {"venue", "extra_expense", "debit_adjustment"}:
            balance -= amount
        else:
            balance += amount
    snapshot.running_cash_balance_cents = balance
    db.add(snapshot)
    db.flush()
    return snapshot
