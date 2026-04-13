from __future__ import annotations

from calendar import monthrange
from datetime import date

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import Group, GroupFinancialEntry, GroupMember, GroupMemberFinancialStatus
from app.repositories.finance import get_entry, get_member_financial_status, get_monthly_entry
from app.services.finance_snapshot_service import rebuild_snapshot
from app.services.finance_summary_service import amount_to_cents, normalize_entry_type, is_expense_entry


def _reference_month_for(today: date | None = None) -> date:
    d = today or utc_now().date()
    return d.replace(day=1)


def _amount_for_group_currency(group: Group, amount: float | int | None) -> tuple[int, str]:
    return amount_to_cents(amount), (group.currency or "BRL").strip().upper()


def create_manual_transaction(
    db: Session,
    *,
    group: Group,
    created_by_user_id: int,
    user_id: int | None,
    player_id: int | None,
    match_id: int | None,
    transaction_type: str,
    amount: float,
    due_date: date | None,
    description: str | None,
    notes: str | None,
) -> GroupFinancialEntry:
    amount_cents, currency = _amount_for_group_currency(group, amount)
    entry = GroupFinancialEntry(
        group_id=group.id,
        user_id=user_id,
        match_id=match_id,
        entry_type=normalize_entry_type(transaction_type or "manual"),
        amount_cents=amount_cents,
        currency=currency,
        status="pending",
        due_date=due_date,
        description=description,
        notes=notes,
        paid=False,
        paid_amount_cents=0,
        confirmed_by_user_id=None,
    )
    db.add(entry)
    try:
        db.flush()
    except IntegrityError as ex:
        raise HTTPException(status_code=409, detail=f"Conflito: {ex.orig}")
    _refresh_month_projection(db, group_id=group.id)
    return entry


def mark_entry_paid(
    db: Session,
    *,
    group: Group,
    transaction_id: int,
    acting_user_id: int,
    amount: float | None,
    payment_method: str | None,
    notes: str | None,
    mark_as_paid: bool,
) -> GroupFinancialEntry:
    entry = get_entry(db, group_id=group.id, entry_id=transaction_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Transaction not found")

    if not mark_as_paid:
        if entry.confirmed_by_user_id is not None and int(entry.confirmed_by_user_id) != int(acting_user_id):
            raise HTTPException(status_code=403, detail="Somente quem confirmou o pagamento pode cancelar")
        entry.paid = False
        entry.status = "pending"
        entry.paid_at = None
        entry.paid_amount_cents = 0
        entry.payment_method = None
        entry.notes = notes or entry.notes
        entry.confirmed_by_user_id = None
    else:
        target_total = int(entry.amount_cents or 0)
        current_paid = int(getattr(entry, "paid_amount_cents", None) or 0)
        if amount is not None and amount <= 0:
            raise HTTPException(status_code=400, detail="O valor pago deve ser maior que zero")
        if entry.paid and current_paid >= target_total and amount is None:
            entry.payment_method = payment_method or entry.payment_method
            entry.notes = notes or entry.notes
            entry.confirmed_by_user_id = acting_user_id
            db.add(entry)
            _refresh_month_projection(db, group_id=group.id)
            return entry
        resolved_paid_total = amount_to_cents(amount) if amount is not None else target_total
        entry.paid_amount_cents = min(max(resolved_paid_total, 0), target_total)
        entry.paid = entry.paid_amount_cents >= target_total
        entry.status = "paid" if entry.paid else "pending"
        entry.paid_at = utc_now() if entry.paid_amount_cents > 0 else None
        entry.payment_method = payment_method or entry.payment_method
        entry.notes = notes or entry.notes
        entry.confirmed_by_user_id = acting_user_id

    db.add(entry)
    _refresh_month_projection(db, group_id=group.id)
    return entry




def ensure_monthly_venue_entry(
    db: Session,
    *,
    group: Group,
    reference_month: date | None = None,
) -> GroupFinancialEntry | None:
    reference = (reference_month or utc_now().date()).replace(day=1)
    venue_amount = float(getattr(group, "venue_cost", 0) or 0)
    if venue_amount <= 0:
        return None

    existing = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group.id)
        .filter(GroupFinancialEntry.user_id.is_(None))
        .filter(GroupFinancialEntry.match_id.is_(None))
        .filter(GroupFinancialEntry.entry_type == "venue")
        .filter(GroupFinancialEntry.period_year == reference.year)
        .filter(GroupFinancialEntry.period_month == reference.month)
        .order_by(GroupFinancialEntry.id.asc())
        .first()
    )

    amount_cents, currency = _amount_for_group_currency(group, venue_amount)
    description = f"Valor do local {reference.month:02d}/{reference.year}"
    due = reference

    if existing is None:
        existing = GroupFinancialEntry(
            group_id=group.id,
            user_id=None,
            match_id=None,
            period_year=reference.year,
            period_month=reference.month,
            entry_type="venue",
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            due_date=due,
            description=description,
            notes="Gerado automaticamente a partir do valor do local do grupo",
            paid=False,
            paid_amount_cents=0,
            confirmed_by_user_id=None,
        )
        db.add(existing)
        db.flush()
        _refresh_month_projection(db, group_id=group.id, reference_month=reference)
        return existing

    changed = False
    if int(existing.amount_cents or 0) != amount_cents:
        existing.amount_cents = amount_cents
        changed = True
    if (existing.currency or "") != currency:
        existing.currency = currency
        changed = True
    if existing.due_date != due:
        existing.due_date = due
        changed = True
    if (existing.description or "") != description:
        existing.description = description
        changed = True
    if changed:
        db.add(existing)
        db.flush()
        _refresh_month_projection(db, group_id=group.id, reference_month=reference)
        return existing
    return None

def generate_monthly_entries(
    db: Session,
    *,
    group: Group,
    year: int,
    month: int,
) -> dict:
    if not group.monthly_cost or (group.monthly_cost or 0) <= 0:
        raise HTTPException(status_code=400, detail="monthly_cost não configurado")
    if not group.payment_due_day:
        raise HTTPException(status_code=400, detail="payment_due_day não configurado")
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month inválido")

    last_day = monthrange(year, month)[1]
    due_day = max(1, min(int(group.payment_due_day), last_day))
    due = date(year, month, due_day)
    created = skipped = 0

    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group.id, GroupMember.status == "active")
        .all()
    )
    for member in members:
        if (member.billing_type or "single").lower() != "monthly":
            continue
        entry = get_monthly_entry(db, group_id=group.id, user_id=int(member.user_id), year=year, month=month)
        if entry is not None:
            skipped += 1
            continue
        amount_cents, currency = _amount_for_group_currency(group, float(group.monthly_cost or 0))
        row = GroupFinancialEntry(
            group_id=group.id,
            user_id=member.user_id,
            match_id=None,
            period_year=year,
            period_month=month,
            entry_type="monthly",
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            due_date=due,
            description=f"Mensalidade {month:02d}/{year}",
            notes="Gerado automaticamente",
            paid=False,
            paid_amount_cents=0,
        )
        db.add(row)
        created += 1
        _upsert_member_financial_status(
            db,
            group_id=group.id,
            player_id=int(member.player_id),
            reference_month=date(year, month, 1),
            billing_type="monthly",
            monthly_fee_due_cents=amount_cents,
            monthly_fee_paid_cents=0,
            is_adimplente=False,
            last_payment_entry_id=None,
        )

    _refresh_month_projection(db, group_id=group.id, reference_month=date(year, month, 1))
    return {"ok": True, "year": year, "month": month, "created": created, "skipped": skipped}


def mark_monthly_member_paid(
    db: Session,
    *,
    group: Group,
    player_id: int,
    user_id: int,
    acting_user_id: int,
    player_name: str,
) -> GroupFinancialEntry:
    now = utc_now().date()
    year, month = now.year, now.month
    last_day = monthrange(year, month)[1]
    due_day = max(1, min(int(group.payment_due_day or last_day), last_day))
    due = date(year, month, due_day)
    amount_cents, currency = _amount_for_group_currency(group, float(group.monthly_cost or 0))

    entry = get_monthly_entry(db, group_id=group.id, user_id=user_id, year=year, month=month)
    if entry is None:
        entry = GroupFinancialEntry(
            group_id=group.id,
            user_id=user_id,
            match_id=None,
            period_year=year,
            period_month=month,
            entry_type="monthly",
            amount_cents=amount_cents,
            currency=currency,
            status="paid",
            due_date=due,
            description=f"Mensalidade {month:02d}/{year} • {player_name}",
            paid=True,
            paid_at=utc_now(),
            paid_amount_cents=amount_cents,
            payment_method="monthly_control",
            confirmed_by_user_id=acting_user_id,
        )
        db.add(entry)
        db.flush()
    else:
        entry.amount_cents = amount_cents
        entry.currency = currency
        entry.due_date = due
        if not entry.paid or int(getattr(entry, "paid_amount_cents", 0) or 0) < amount_cents:
            entry.paid_at = utc_now()
        entry.status = "paid"
        entry.paid = True
        entry.paid_amount_cents = amount_cents
        entry.payment_method = "monthly_control"
        entry.confirmed_by_user_id = acting_user_id
        db.add(entry)

    _upsert_member_financial_status(
        db,
        group_id=group.id,
        player_id=player_id,
        reference_month=date(year, month, 1),
        billing_type="monthly",
        monthly_fee_due_cents=amount_cents,
        monthly_fee_paid_cents=amount_cents,
        is_adimplente=True,
        last_payment_entry_id=entry.id,
    )
    _refresh_month_projection(db, group_id=group.id, reference_month=date(year, month, 1))
    return entry


def unmark_monthly_member_paid(
    db: Session,
    *,
    group: Group,
    player_id: int,
    user_id: int,
    acting_user_id: int,
) -> GroupFinancialEntry:
    now = utc_now().date()
    year, month = now.year, now.month
    entry = get_monthly_entry(db, group_id=group.id, user_id=user_id, year=year, month=month)
    if entry is None:
        raise HTTPException(status_code=404, detail="Pagamento mensal não encontrado")
    if entry.confirmed_by_user_id != acting_user_id:
        raise HTTPException(status_code=403, detail="Somente quem marcou o pagamento pode desfazer")
    entry.paid = False
    entry.status = "pending"
    entry.paid_at = None
    entry.paid_amount_cents = 0
    entry.payment_method = None
    entry.confirmed_by_user_id = None
    db.add(entry)

    status = get_member_financial_status(db, group.id, player_id, date(year, month, 1))
    if status is not None:
        status.monthly_fee_paid_cents = 0
        status.is_adimplente = False
        status.last_payment_entry_id = None
        db.add(status)

    _refresh_month_projection(db, group_id=group.id, reference_month=date(year, month, 1))
    return entry


def _upsert_member_financial_status(
    db: Session,
    *,
    group_id: str,
    player_id: int,
    reference_month: date,
    billing_type: str,
    monthly_fee_due_cents: int,
    monthly_fee_paid_cents: int,
    is_adimplente: bool,
    last_payment_entry_id: int | None,
) -> GroupMemberFinancialStatus:
    row = get_member_financial_status(db, group_id, player_id, reference_month)
    if row is None:
        row = GroupMemberFinancialStatus(
            group_id=group_id,
            player_id=player_id,
            reference_month=reference_month,
        )
    row.billing_type = billing_type
    row.monthly_fee_due_cents = monthly_fee_due_cents
    row.monthly_fee_paid_cents = monthly_fee_paid_cents
    row.is_adimplente = is_adimplente
    row.last_payment_entry_id = last_payment_entry_id
    db.add(row)
    db.flush()
    return row


def _refresh_month_projection(db: Session, *, group_id: str, reference_month: date | None = None) -> None:
    rebuild_snapshot(db, group_id=group_id, reference_month=reference_month or _reference_month_for())
