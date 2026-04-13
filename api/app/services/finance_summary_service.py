from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import Group, GroupFinancialEntry, GroupMember, Player, User
from app.repositories.finance import get_monthly_entry, list_group_entries, list_user_entries


def amount_to_cents(amount: float | int | None) -> int:
    try:
        return int(round(float(amount or 0) * 100))
    except Exception:
        return 0


def cents_to_amount(cents: int | None) -> float:
    try:
        return int(cents or 0) / 100.0
    except Exception:
        return 0.0


def entry_amount_cents(entry: GroupFinancialEntry) -> int:
    return abs(int(entry.amount_cents or 0))


def entry_paid_amount_cents(entry: GroupFinancialEntry, *, status: str | None = None) -> int:
    resolved_status = (status or entry.status or "pending").lower()
    paid_amount = int(getattr(entry, "paid_amount_cents", None) or 0)
    if paid_amount == 0 and resolved_status == "paid":
        paid_amount = entry_amount_cents(entry)
    return abs(paid_amount)


def normalize_entry_type(value: str | None) -> str:
    raw = (value or "manual").strip().lower()
    aliases = {
        "mensalidade": "monthly",
        "avulso": "single",
        "single_match_payment": "single",
        "single_guest": "single",
        "guest_single": "single",
        "multa": "fine",
        "venue_cost": "venue",
        "valor_local": "venue",
        "quadra": "venue",
        "extra_expense": "extra_expense",
        "outra_despesa": "extra_expense",
        "expense": "extra_expense",
        "manual": "extra_expense",
    }
    return aliases.get(raw, raw)


def is_expense_entry(entry_type: str | None) -> bool:
    return normalize_entry_type(entry_type) in ("venue", "extra_expense", "debit_adjustment")


def month_window(reference: date | None = None) -> tuple[date, date]:
    current = reference or utc_now().date()
    start = current.replace(day=1)
    if current.month == 12:
        next_month = current.replace(year=current.year + 1, month=1, day=1)
    else:
        next_month = current.replace(month=current.month + 1, day=1)
    return start, next_month.fromordinal(next_month.toordinal() - 1)


def _safe_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def month_matches_entry(e: GroupFinancialEntry, start: date, end: date) -> bool:
    if e.period_year and e.period_month:
        return e.period_year == start.year and e.period_month == start.month
    ref_dt = _safe_date(e.paid_at) or _safe_date(e.due_date) or _safe_date(e.created_at)
    if ref_dt is None:
        return False
    return start <= ref_dt <= end


def entry_remaining_amount_cents(entry: GroupFinancialEntry, *, status: str | None = None) -> int:
    resolved_status = (status or entry.status or "pending").lower()
    if resolved_status in ("paid", "cancelled", "forgiven"):
        return 0
    return max(entry_amount_cents(entry) - entry_paid_amount_cents(entry, status=resolved_status), 0)


def build_finance_overview(entries: list[GroupFinancialEntry], *, reference: date | None = None) -> dict[str, Any]:
    current = reference or utc_now().date()
    start, end = month_window(current.replace(day=1))

    alltime_income_paid = 0
    alltime_expense_paid = 0
    alltime_pending = 0
    month_paid_by_type: dict[str, int] = defaultdict(int)
    month_received_paid = 0
    month_expenses_paid = 0
    month_open_income = 0
    month_open_expense = 0
    next_due_dates: list[date] = []

    for entry in entries:
        status = (entry.status or "pending").lower()
        entry_type = normalize_entry_type(entry.entry_type)
        expense = is_expense_entry(entry_type)
        paid_amount = entry_paid_amount_cents(entry, status=status)
        remaining = entry_remaining_amount_cents(entry, status=status)
        in_month = month_matches_entry(entry, start, end)

        if status == "paid":
            if expense:
                alltime_expense_paid += paid_amount
                if in_month:
                    month_expenses_paid += paid_amount
            else:
                alltime_income_paid += paid_amount
                if in_month:
                    month_received_paid += paid_amount
            if in_month:
                month_paid_by_type[entry_type] += paid_amount
        elif status not in ("cancelled", "forgiven"):
            alltime_pending += remaining
            if in_month:
                if expense:
                    month_open_expense += remaining
                else:
                    month_open_income += remaining

        if status not in ("paid", "cancelled", "forgiven"):
            dd = _safe_date(entry.due_date)
            if dd is not None:
                next_due_dates.append(dd)

    m_monthly = month_paid_by_type.get("monthly", 0)
    m_single = month_paid_by_type.get("single", 0)
    m_fine = month_paid_by_type.get("fine", 0)
    m_credit = month_paid_by_type.get("credit_adjustment", 0)
    m_venue = month_paid_by_type.get("venue", 0)
    m_extra = month_paid_by_type.get("extra_expense", 0)
    m_debit = month_paid_by_type.get("debit_adjustment", 0)
    received_sub = m_monthly + m_single + m_fine + m_credit
    expenses_sub = m_venue + m_extra + m_debit

    return {
        "reference_month": start.isoformat(),
        "reference_year": start.year,
        "reference_month_number": start.month,
        "cashflow_total_cents": alltime_income_paid - alltime_expense_paid,
        "cash_in_box_cents": alltime_income_paid - alltime_expense_paid,
        "total_income_paid_cents": alltime_income_paid,
        "total_expense_paid_cents": alltime_expense_paid,
        "total_pending_cents": alltime_pending,
        "monthly_members_total_cents": m_monthly,
        "single_matches_total_cents": m_single,
        "fines_total_cents": m_fine,
        "credit_adjustments_total_cents": m_credit,
        "received_subtotal_cents": received_sub,
        "venue_total_cents": m_venue,
        "extra_expenses_total_cents": m_extra,
        "debit_adjustments_total_cents": m_debit,
        "expenses_subtotal_cents": expenses_sub,
        "month_received_paid_cents": month_received_paid,
        "month_expenses_paid_cents": month_expenses_paid,
        "month_open_income_cents": month_open_income,
        "month_open_expense_cents": month_open_expense,
        "month_result_cents": received_sub - expenses_sub,
        "next_due_date": min(next_due_dates).isoformat() if next_due_dates else None,
    }


def wallet_summary(entries: list[GroupFinancialEntry]) -> dict[str, Any]:
    pending_total = 0
    paid_total = 0
    fines_pending = 0
    monthly_pending = 0
    single_pending = 0
    balance = 0
    for e in entries:
        status = (e.status or "pending").lower()
        amount = entry_amount_cents(e)
        paid_amount = entry_paid_amount_cents(e, status=status)
        if status == "paid":
            signed = -paid_amount if is_expense_entry(e.entry_type) else paid_amount
            paid_total += paid_amount
            balance += signed
            continue
        if status in ("cancelled", "forgiven"):
            continue
        remaining = max(amount - paid_amount, 0)
        pending_total += remaining
        balance -= remaining
        entry_type = normalize_entry_type(e.entry_type)
        if entry_type == "fine":
            fines_pending += remaining
        if entry_type == "monthly":
            monthly_pending += remaining
        if entry_type == "single":
            single_pending += remaining
    return {
        "pending_total": cents_to_amount(pending_total),
        "paid_total": cents_to_amount(paid_total),
        "fines_pending": cents_to_amount(fines_pending),
        "monthly_due": monthly_pending > 0,
        "monthly_pending": cents_to_amount(monthly_pending),
        "single_charges": cents_to_amount(single_pending),
        "balance_total": cents_to_amount(balance),
    }


def compute_cashflow(entries: list[GroupFinancialEntry]) -> float:
    balance = 0
    for e in entries:
        if (e.status or "pending").lower() != "paid":
            continue
        amount = entry_paid_amount_cents(e)
        balance += -amount if is_expense_entry(e.entry_type) else amount
    return cents_to_amount(balance)


def build_summary(
    db: Session,
    *,
    group: Group,
    current_user_id: int,
    year: int | None = None,
    month: int | None = None,
) -> dict[str, Any]:
    entries = list_group_entries(db, group.id)

    now = utc_now().date()
    ref_year = year or now.year
    ref_month = month or now.month
    overview = build_finance_overview(entries, reference=date(ref_year, ref_month, 1))

    return {
        "group_id": group.id,
        "currency": group.currency,
        "cashflow_total": cents_to_amount(overview["cashflow_total_cents"]),
        "cash_in_box": cents_to_amount(overview["cash_in_box_cents"]),
        "total_income_paid": cents_to_amount(overview["total_income_paid_cents"]),
        "total_expense_paid": cents_to_amount(overview["total_expense_paid_cents"]),
        "total_pending": cents_to_amount(overview["total_pending_cents"]),
        "month_reference": overview["reference_month"],
        "month_year": overview["reference_year"],
        "month_month": overview["reference_month_number"],
        "monthly_members_total": cents_to_amount(overview["monthly_members_total_cents"]),
        "single_matches_total": cents_to_amount(overview["single_matches_total_cents"]),
        "fines_total": cents_to_amount(overview["fines_total_cents"]),
        "credit_adjustments_total": cents_to_amount(overview["credit_adjustments_total_cents"]),
        "received_subtotal": cents_to_amount(overview["received_subtotal_cents"]),
        "venue_total": cents_to_amount(overview["venue_total_cents"]),
        "extra_expenses_total": cents_to_amount(overview["extra_expenses_total_cents"]),
        "debit_adjustments_total": cents_to_amount(overview["debit_adjustments_total_cents"]),
        "expenses_subtotal": cents_to_amount(overview["expenses_subtotal_cents"]),
        "month_result": cents_to_amount(overview["month_result_cents"]),
        "next_due_date": overview["next_due_date"],
        "payment_method": group.payment_method,
        "payment_key": group.payment_key,
        "payment_due_day": group.payment_due_day,
        "total_paid": cents_to_amount(overview["total_income_paid_cents"]),
    }


def build_reports(db: Session, *, group: Group) -> dict[str, Any]:
    entries = list_group_entries(db, group.id)
    now = utc_now().date()
    start, end = month_window(date(now.year, now.month, 1))
    overview = build_finance_overview(entries, reference=date(now.year, now.month, 1))

    total_expected = 0
    total_received = 0
    total_pending = 0
    total_fines = 0
    total_match_revenue = 0
    by_type: dict[str, dict[str, float]] = {}
    received = {"monthly_fees": 0, "single_payments": 0, "fines": 0, "adjustments": 0, "subtotal": 0}
    expenses = {"venue_cost": 0, "extra_expenses": 0, "debit_adjustments": 0, "subtotal": 0}

    for e in entries:
        status = (e.status or "pending").lower()
        amount = entry_amount_cents(e)
        paid_amount = entry_paid_amount_cents(e, status=status)
        entry_type = normalize_entry_type(e.entry_type)
        expense = is_expense_entry(entry_type)

        bucket = by_type.setdefault(entry_type, {"expected": 0.0, "received": 0.0, "pending": 0.0})
        bucket["expected"] += cents_to_amount(amount)
        total_expected += amount

        if status == "paid":
            bucket["received"] += cents_to_amount(paid_amount)
            total_received += paid_amount
            if entry_type == "monthly":
                received["monthly_fees"] += paid_amount
            elif entry_type == "single":
                received["single_payments"] += paid_amount
                total_match_revenue += paid_amount
            elif entry_type == "fine":
                received["fines"] += paid_amount
            elif entry_type == "credit_adjustment":
                received["adjustments"] += paid_amount
            elif entry_type == "venue":
                expenses["venue_cost"] += paid_amount
            elif entry_type == "extra_expense":
                expenses["extra_expenses"] += paid_amount
            elif entry_type == "debit_adjustment":
                expenses["debit_adjustments"] += paid_amount
        elif status not in ("cancelled", "forgiven"):
            delta = entry_remaining_amount_cents(e, status=status)
            bucket["pending"] += cents_to_amount(delta)
            total_pending += delta

        if entry_type == "fine":
            total_fines += amount

    received["subtotal"] = received["monthly_fees"] + received["single_payments"] + received["fines"] + received["adjustments"]
    expenses["subtotal"] = expenses["venue_cost"] + expenses["extra_expenses"] + expenses["debit_adjustments"]

    return {
        "group_id": group.id,
        "currency": group.currency,
        "total_to_receive": cents_to_amount(total_expected),
        "total_received": cents_to_amount(total_received),
        "total_pending": cents_to_amount(total_pending),
        "fines_generated": cents_to_amount(total_fines),
        "match_revenue": cents_to_amount(total_match_revenue),
        "group_balance": cents_to_amount(overview["cashflow_total_cents"]),
        "income_total": cents_to_amount(overview["total_income_paid_cents"]),
        "expense_total": cents_to_amount(overview["total_expense_paid_cents"]),
        "open_income_total": cents_to_amount(overview["month_open_income_cents"]),
        "open_expense_total": cents_to_amount(overview["month_open_expense_cents"]),
        "received": {k: cents_to_amount(v) for k, v in received.items()},
        "expenses": {k: cents_to_amount(v) for k, v in expenses.items()},
        "by_type": by_type,
        "month": {
            "reference_month": start.isoformat(),
            "received_total": cents_to_amount(overview["month_received_paid_cents"]),
            "expense_total": cents_to_amount(overview["month_expenses_paid_cents"]),
            "result": cents_to_amount(overview["month_result_cents"]),
            "next_due_date": overview["next_due_date"],
        },
    }


def _user_display_name(u: User | None) -> str | None:
    if u is None:
        return None
    try:
        if hasattr(u, "profile") and u.profile is not None:
            return getattr(u.profile, "name", None) or getattr(u.profile, "full_name", None)
    except Exception:
        pass
    return getattr(u, "name", None) or getattr(u, "email", None)


def build_billing_members(
    db: Session,
    *,
    group: Group,
    current_user_id: int,
) -> dict[str, Any]:
    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group.id, GroupMember.status == "active")
        .order_by(GroupMember.id.asc())
        .all()
    )

    now = utc_now().date()
    yr, mo = now.year, now.month
    start, end = month_window(date(yr, mo, 1))

    monthly_list: list[dict] = []
    single_list: list[dict] = []

    for m in members:
        billing = (m.billing_type or "single").lower()
        player = db.query(Player).filter(Player.id == m.player_id).first()
        player_name = player.name if player else f"Jogador #{m.player_id}"

        base = {
            "user_id": int(m.user_id),
            "player_id": int(m.player_id),
            "player_name": player_name,
            "billing_type": billing,
        }

        if billing == "monthly":
            entry = get_monthly_entry(db, group_id=group.id, user_id=int(m.user_id), year=yr, month=mo)
            confirmer = db.query(User).filter(User.id == entry.confirmed_by_user_id).first() if entry and entry.confirmed_by_user_id else None
            monthly_list.append({
                **base,
                "amount": float(group.monthly_cost or 0),
                "paid": bool(entry.paid) if entry else False,
                "due_date": entry.due_date.isoformat() if entry and entry.due_date else None,
                "confirmed_by_user_id": entry.confirmed_by_user_id if entry else None,
                "confirmed_by_user_name": _user_display_name(confirmer),
                "can_unmark": bool(entry and entry.confirmed_by_user_id == current_user_id),
                "entry_id": entry.id if entry else None,
            })
        else:
            user_entries = list_user_entries(db, group.id, int(m.user_id))
            pending_amount = 0
            paid_amount = 0
            for e in user_entries:
                if not month_matches_entry(e, start, end):
                    continue
                et = normalize_entry_type(e.entry_type)
                if et in ("monthly", "venue", "extra_expense", "debit_adjustment"):
                    continue
                st = (e.status or "pending").lower()
                if st == "paid":
                    paid_amount += entry_paid_amount_cents(e)
                elif st not in ("cancelled", "forgiven"):
                    pending_amount += max(entry_amount_cents(e) - entry_paid_amount_cents(e), 0)

            single_list.append({
                **base,
                "month_paid": cents_to_amount(paid_amount),
                "month_pending": cents_to_amount(pending_amount),
                "financial_status": "adimplente" if pending_amount == 0 else "inadimplente",
            })

    return {
        "group_id": group.id,
        "currency": group.currency,
        "year": yr,
        "month": mo,
        "monthly_members": monthly_list,
        "single_members": single_list,
    }


def build_month_rollup(db: Session, *, group_id: str, reference_month: date) -> dict:
    start, end = month_window(reference_month)
    rows = [e for e in list_group_entries(db, group_id) if month_matches_entry(e, start, end)]
    totals = defaultdict(int)
    for row in rows:
        if (row.status or "pending").lower() != "paid":
            continue
        amount = entry_paid_amount_cents(row)
        totals[normalize_entry_type(row.entry_type)] += amount
    revenue = totals["monthly"] + totals["single"] + totals["fine"]
    expenses_val = totals["venue"] + totals["extra_expense"]
    return {
        "reference_month": start.isoformat(),
        "totals": dict(totals),
        "revenue_cents": revenue,
        "expenses_cents": expenses_val,
        "month_result_cents": revenue - expenses_val,
    }


def build_player_ledger(db: Session, *, group: Group, player_id: int, player_name: str, user_id: int, serializer) -> dict[str, Any]:
    entries = list_user_entries(db, group.id, user_id)
    return {
        "group_id": group.id,
        "player_id": player_id,
        "player_name": player_name,
        "currency": group.currency,
        "items": [serializer(e) for e in entries],
    }


def build_wallet(db: Session, *, group: Group, user_id: int, serializer) -> dict[str, Any]:
    entries = list_user_entries(db, group.id, user_id)
    summary = wallet_summary(entries)
    summary.update({
        "group_id": group.id,
        "currency": group.currency,
        "ledger_count": len(entries),
        "recent_ledger": [serializer(e) for e in entries[:10]],
    })
    return summary
