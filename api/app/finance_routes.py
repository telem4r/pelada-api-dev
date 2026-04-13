from __future__ import annotations
from app.core.logging import configure_logging, log_event
logger = configure_logging()

from calendar import monthrange
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Group, GroupFinancialEntry, GroupMember, Player, User
from app.permissions import get_group_member, require_group_admin
from app.communication_utils import create_notification, notification_allowed
from app.core.time import utc_now, utc_today
from app.security import get_current_user as get_current_user_id
from app.repositories.finance import get_monthly_entry, list_group_entries_ordered, query_group_entries
from app.services.finance_entries_service import (
    create_manual_transaction,
    ensure_monthly_venue_entry,
    generate_monthly_entries,
    mark_entry_paid,
    mark_monthly_member_paid,
    unmark_monthly_member_paid,
)
from app.services.finance_summary_service import (
    build_billing_members,
    build_player_ledger,
    build_reports,
    build_summary,
    build_wallet,
    compute_cashflow as svc_compute_cashflow,
    month_window as svc_month_window,
    normalize_entry_type as svc_normalize_entry_type,
    wallet_summary as svc_wallet_summary,
)


router = APIRouter(tags=["finance"])
group_finance = APIRouter(prefix="/groups/{group_id}/finance", tags=["finance"])
legacy_finance = APIRouter(prefix="/finance", tags=["finance"], deprecated=True)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _require_group(db: Session, group_id: str) -> Group:
    g = db.query(Group).filter(Group.id == group_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Group not found")
    return g


def _amount_to_cents(amount: float | int | None) -> int:
    try:
        return int(round(float(amount or 0) * 100))
    except Exception:
        return 0


def _cents_to_amount(cents: int | None) -> float:
    try:
        return int(cents or 0) / 100.0
    except Exception:
        return 0.0


def _is_active_status(value: str | None) -> bool:
    return (value or "pending").strip().lower() not in ("cancelled", "forgiven")


def _normalize_entry_type(value: Optional[str]) -> str:
    raw = (value or "manual").strip().lower()
    aliases = {
        "mensalidade": "monthly",
        "avulso": "single",
        "single_match_payment": "single",
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


def _is_expense_entry(entry_type: Optional[str]) -> bool:
    return _normalize_entry_type(entry_type) in ("venue", "extra_expense", "debit_adjustment")


def _entry_category(entry_type: Optional[str]) -> str:
    t = _normalize_entry_type(entry_type)
    mapping = {
        "monthly": "mensalidade",
        "single": "avulso",
        "fine": "multa",
        "venue": "valor_local",
        "extra_expense": "outra_despesa",
        "credit_adjustment": "ajuste",
        "debit_adjustment": "ajuste",
    }
    return mapping.get(t, "outra_despesa" if _is_expense_entry(t) else "ajuste")


def _entry_direction(entry_type: Optional[str]) -> str:
    return "debit" if _is_expense_entry(entry_type) else "credit"


def _month_matches_entry(e: GroupFinancialEntry, start: date, end: date) -> bool:
    if e.period_year and e.period_month:
        return e.period_year == start.year and e.period_month == start.month
    ref_dt = e.paid_at or e.due_date or (e.created_at.date() if e.created_at else None)
    if ref_dt is None:
        return False
    if isinstance(ref_dt, datetime):
        ref_dt = ref_dt.date()
    return start <= ref_dt <= end


def _upsert_due_tomorrow_notification(db: Session, *, group: Group, user_id: int) -> None:
    today = utc_today()
    tomorrow = today.fromordinal(today.toordinal() + 1)
    entry = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group.id)
        .filter(GroupFinancialEntry.user_id == user_id)
        .filter(GroupFinancialEntry.entry_type == "monthly")
        .filter(GroupFinancialEntry.due_date == tomorrow)
        .filter(GroupFinancialEntry.status.notin_(("paid", "cancelled", "forgiven")))
        .order_by(GroupFinancialEntry.id.desc())
        .first()
    )
    if not entry:
        return
    if not notification_allowed(db, user_id, "finance"):
        return
    create_notification(
        db,
        user_id=user_id,
        type="monthly_fee_due_tomorrow",
        title="Mensalidade vence amanhã",
        message=f"A mensalidade do grupo {group.name} vence amanhã.",
        external_key=f"monthly_due_tomorrow:{group.id}:{user_id}:{tomorrow.isoformat()}",
        payload={"group_id": group.id, "entry_id": entry.id, "due_date": tomorrow.isoformat()},
    )


def _user_display_name(u: User | None) -> Optional[str]:
    if u is None:
        return None
    try:
        if hasattr(u, "profile") and u.profile is not None:
            return getattr(u.profile, "name", None) or getattr(u.profile, "full_name", None)
    except Exception:
        pass
    return getattr(u, "name", None) or getattr(u, "email", None)




def _extract_guest_meta(notes: str | None) -> tuple[int | None, str | None]:
    raw = (notes or "").strip()
    if not raw:
        return None, None
    guest_id = None
    guest_name = None
    if "guest_id:" in raw:
        try:
            guest_id = int(raw.split("guest_id:", 1)[1].split(";", 1)[0].strip())
        except Exception:
            guest_id = None
    if "guest_name:" in raw:
        try:
            guest_name = raw.split("guest_name:", 1)[1].split(";", 1)[0].strip() or None
        except Exception:
            guest_name = None
    return guest_id, guest_name


def _public_notes(notes: str | None) -> str | None:
    raw = (notes or "").strip()
    if not raw or raw.startswith("guest_id:"):
        return None
    return raw

def _manual_expense_display_name(entry_type: Optional[str]) -> Optional[str]:
    normalized = _normalize_entry_type(entry_type)
    if normalized == "venue":
        return "Valor do local"
    if normalized == "extra_expense":
        return "Outras despesas"
    return None


def _entry_to_out(e: GroupFinancialEntry, *, current_user_id: int | None = None) -> Dict[str, Any]:
    user_name = _user_display_name(e.user)
    user_avatar = None
    try:
        if e.user is not None and hasattr(e.user, "profile") and e.user.profile is not None:
            user_avatar = getattr(e.user.profile, "avatar_url", None)
    except Exception:
        pass

    guest_id, guest_name = _extract_guest_meta(getattr(e, "notes", None))
    if user_name is None and guest_name:
        user_name = guest_name
    confirmed_by_user_name = None
    if getattr(e, "confirmed_by", None) is not None:
        confirmed_by_user_name = _user_display_name(e.confirmed_by)

    paid_amount = getattr(e, "paid_amount_cents", None)
    if paid_amount in (None, 0) and bool(e.paid):
        paid_amount = e.amount_cents

    amount_cents = abs(int(e.amount_cents or 0))
    public_entry_type = "single" if (e.entry_type or "").lower() in {"single_guest", "guest_single"} else e.entry_type
    resolved_type = _normalize_entry_type(public_entry_type)
    if user_name is None:
        user_name = _manual_expense_display_name(resolved_type)
    display_status = _display_status_for_entry(e)
    can_unmark = bool(
        (e.status or "pending").lower() == "paid"
        and current_user_id is not None
        and e.confirmed_by_user_id is not None
        and int(e.confirmed_by_user_id) == int(current_user_id)
    )
    return {
        "id": e.id,
        "group_id": e.group_id,
        "user_id": e.user_id,
        "match_id": e.match_id,
        "entry_type": public_entry_type,
        "type": "expense" if _is_expense_entry(resolved_type) else "income",
        "amount": _cents_to_amount(amount_cents),
        "currency": e.currency,
        "status": e.status,
        "display_status": display_status,
        "raw_status": e.status,
        "is_overdue": display_status == "overdue",
        "due_date": e.due_date.isoformat() if e.due_date else None,
        "description": e.description,
        "paid": bool(e.paid),
        "paid_at": e.paid_at.isoformat() if e.paid_at else None,
        "paid_amount": _cents_to_amount(paid_amount),
        "payment_method": getattr(e, "payment_method", None),
        "notes": _public_notes(getattr(e, "notes", None)),
        "guest_id": guest_id,
        "guest_name": guest_name,
        "confirmed_by_user_id": e.confirmed_by_user_id,
        "confirmed_by_user_name": confirmed_by_user_name,
        "can_unmark": can_unmark,
        "user_name": user_name,
        "user_avatar_url": user_avatar,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


def _compute_cashflow(entries: List[GroupFinancialEntry]) -> float:
    inflow = 0
    outflow = 0
    for e in entries:
        if (e.status or "pending").lower() != "paid":
            continue
        amount = abs(int(getattr(e, "paid_amount_cents", None) or e.amount_cents or 0))
        if _is_expense_entry(getattr(e, "entry_type", None)):
            outflow += amount
        else:
            inflow += amount
    return _cents_to_amount(inflow - outflow)


def _resolve_player_and_user(db: Session, *, group_id: str, player_id: int) -> tuple[Player, int]:
    p = db.query(Player).filter(Player.id == player_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Player not found")
    gm = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.player_id == player_id)
        .first()
    )
    if not gm:
        raise HTTPException(status_code=404, detail="Player is not a member of this group")
    return p, int(p.owner_id)




def _ensure_current_month_baseline(db: Session, group: Group) -> bool:
    changed = False
    if ensure_monthly_venue_entry(db, group=group) is not None:
        changed = True
    if changed:
        db.flush()
    return changed


def _ensure_finance_automation(db: Session, group: Group, *, reference: Optional[date] = None) -> dict[str, int]:
    """Best-effort automation using existing schema only.

    Creates the monthly charges for the reference month when the group is hybrid,
    has a monthly cost and a due day configured. No location or user-sensitive data
    is persisted beyond the financial entries already required by the product.
    """
    created = 0
    skipped = 0
    ref = reference or utc_today()
    if not _is_hybrid_group_type(group.group_type):
        return {"created": 0, "skipped": 0}
    if not group.payment_due_day or not group.monthly_cost or float(group.monthly_cost or 0) <= 0:
        return {"created": 0, "skipped": 0}
    try:
        result = generate_monthly_entries(db, group=group, year=ref.year, month=ref.month)
        created = int(result.get("created", 0) or 0)
        skipped = int(result.get("skipped", 0) or 0)
        if created:
            db.flush()
            log_event(logger, "finance_automation_monthly_generated", group_id=group.id, year=ref.year, month=ref.month, created=created, skipped=skipped)
    except HTTPException:
        # If config is incomplete or generation is not applicable, keep reads resilient.
        pass
    return {"created": created, "skipped": skipped}


def _display_status_for_entry(entry: GroupFinancialEntry) -> str:
    status = (entry.status or "pending").lower()
    if status in ("paid", "cancelled", "forgiven"):
        return status
    due_date = getattr(entry, "due_date", None)
    if due_date is not None and due_date < utc_today():
        return "overdue"
    return "pending"
def _wallet_summary(entries: List[GroupFinancialEntry]) -> Dict[str, Any]:
    pending_total = 0
    paid_total = 0
    fines_pending = 0
    monthly_pending = 0
    single_pending = 0
    balance = 0
    for e in entries:
        status = (e.status or "pending").lower()
        amount = int(e.amount_cents or 0)
        paid_amount = int(getattr(e, "paid_amount_cents", None) or (amount if status == "paid" else 0))
        if status == "paid":
            paid_total += paid_amount
            balance += paid_amount
            continue
        if status in ("cancelled", "forgiven"):
            continue
        pending_total += max(amount - paid_amount, 0)
        balance -= max(amount - paid_amount, 0)
        if (e.entry_type or "").lower() == "fine":
            fines_pending += max(amount - paid_amount, 0)
        if (e.entry_type or "").lower() == "monthly":
            monthly_pending += max(amount - paid_amount, 0)
        if (e.entry_type or "").lower() == "single":
            single_pending += max(amount - paid_amount, 0)
    return {
        "pending_total": _cents_to_amount(pending_total),
        "paid_total": _cents_to_amount(paid_total),
        "fines_pending": _cents_to_amount(fines_pending),
        "monthly_due": monthly_pending > 0,
        "monthly_pending": _cents_to_amount(monthly_pending),
        "single_charges": _cents_to_amount(single_pending),
        "balance_total": _cents_to_amount(balance),
    }


def _ledger_for_user(db: Session, group_id: str, user_id: int) -> List[GroupFinancialEntry]:
    return (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id, GroupFinancialEntry.user_id == user_id)
        .order_by(GroupFinancialEntry.due_date.desc().nullslast(), GroupFinancialEntry.created_at.desc())
        .all()
    )


def _parse_iso_date(raw: Optional[str], *, field_name: str) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field_name} inválido")


def _normalize_group_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    return raw.replace("í", "i").replace("é", "e").replace("á", "a").replace("ó", "o").replace("ú", "u")


def _is_hybrid_group_type(value: Optional[str]) -> bool:
    norm = _normalize_group_type(value)
    return "hibrid" in norm or "hybrid" in norm


def _month_window(ref: Optional[date] = None) -> tuple[date, date]:
    today = ref or utc_today()
    start = date(today.year, today.month, 1)
    end = date(today.year, today.month, monthrange(today.year, today.month)[1])
    return start, end


def _monthly_entry_for_user(db: Session, group_id: str, user_id: int, *, year: int, month: int) -> Optional[GroupFinancialEntry]:
    return (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .filter(GroupFinancialEntry.user_id == user_id)
        .filter(GroupFinancialEntry.entry_type == "monthly")
        .filter(GroupFinancialEntry.period_year == year)
        .filter(GroupFinancialEntry.period_month == month)
        .first()
    )


# ---------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------


class EntryCreateIn(BaseModel):
    user_id: Optional[int] = None
    match_id: Optional[int] = None
    entry_type: str = Field(default="manual")
    amount: float
    due_date: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None


class ManualTransactionIn(BaseModel):
    user_id: Optional[int] = None
    player_id: Optional[int] = None
    match_id: Optional[int] = None
    transaction_type: str = Field(default="manual")
    amount: float
    due_date: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None


class MarkPaidIn(BaseModel):
    amount: Optional[float] = None
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    mark_as_paid: bool = True


class GenerateMonthlyIn(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None


class DueDayIn(BaseModel):
    due_day: int = Field(ge=1, le=31)


class FinanceSettingsIn(BaseModel):
    payment_method: Optional[str] = None
    payment_key: Optional[str] = None
    due_day: Optional[int] = Field(default=None, ge=1, le=31)


class FinanceAutomationStatusOut(BaseModel):
    group_id: str
    automation_ready: bool
    automation_enabled: bool
    reference_year: int
    reference_month: int
    created_now: int = 0
    skipped_now: int = 0
    monthly_members_count: int = 0
    generated_entries_count: int = 0
    paid_entries_count: int = 0
    pending_entries_count: int = 0
    overdue_entries_count: int = 0
    due_day: Optional[int] = None
    monthly_cost: float = 0
    message: str


class QuickAccessFinanceGroupOut(BaseModel):
    group_id: str
    group_name: str
    currency: str
    role: str
    status: str
    group_balance: Optional[float] = None
    my_pending_total: Optional[float] = None
    my_balance_total: Optional[float] = None
    my_financial_status: Optional[str] = None




class MonthlyMemberOut(BaseModel):
    user_id: int
    player_id: int
    player_name: str
    billing_type: str
    amount: float
    paid: bool
    status: Optional[str] = None
    display_status: Optional[str] = None
    is_overdue: bool = False
    automation_source: Optional[str] = None
    due_date: Optional[str] = None
    confirmed_by_user_id: Optional[int] = None
    confirmed_by_user_name: Optional[str] = None
    can_unmark: bool = False
    entry_id: Optional[int] = None



@router.get("/groups/finance/quick-access", response_model=List[QuickAccessFinanceGroupOut])
def groups_finance_quick_access(
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    player = (
        db.query(Player)
        .filter(Player.owner_id == current_user_id)
        .order_by(Player.id.asc())
        .first()
    )
    if not player:
        return []

    memberships = (
        db.query(GroupMember, Group)
        .join(Group, Group.id == GroupMember.group_id)
        .filter(GroupMember.player_id == player.id)
        .filter(GroupMember.status == "active")
        .order_by(Group.created_at.desc())
        .all()
    )

    out: List[QuickAccessFinanceGroupOut] = []
    for gm, group in memberships:
        role = (gm.role or "member").strip().lower() or "member"
        status = (gm.status or "active").strip().lower() or "active"
        entries = db.query(GroupFinancialEntry).filter(GroupFinancialEntry.group_id == group.id).all()
        wallet_entries = [e for e in entries if int(e.user_id or 0) == int(current_user_id)]
        wallet = _wallet_summary(wallet_entries)

        group_balance = _compute_cashflow(entries) if role in ("owner", "admin") else None

        if wallet["balance_total"] > 0:
            my_status = "credito"
        elif wallet["pending_total"] > 0 or wallet["monthly_due"] or wallet["balance_total"] < 0:
            my_status = "devedor"
        else:
            my_status = "adimplente"

        out.append(QuickAccessFinanceGroupOut(
            group_id=str(group.id),
            group_name=group.name,
            currency=group.currency,
            role=role,
            status=status,
            group_balance=group_balance,
            my_pending_total=wallet["pending_total"],
            my_balance_total=wallet["balance_total"],
            my_financial_status=my_status,
        ))

    return out


# ---------------------------------------------------------------------
# Advanced endpoints
# ---------------------------------------------------------------------


@group_finance.get("/me")
def group_finance_me(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    group, _ = get_group_member(db, group_id, current_user_id)
    _upsert_due_tomorrow_notification(db, group=group, user_id=current_user_id)
    db.commit()
    return build_wallet(db, group=group, user_id=current_user_id, serializer=lambda e: _entry_to_out(e, current_user_id=current_user_id))


@group_finance.get("/players/{player_id}/ledger")
def group_finance_player_ledger(
    group_id: str,
    player_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    group, gm = get_group_member(db, group_id, current_user_id)
    player, user_id = _resolve_player_and_user(db, group_id=group_id, player_id=player_id)
    if gm.role not in ("owner", "admin") and int(player.owner_id) != int(current_user_id):
        raise HTTPException(status_code=403, detail="Sem permissão para ver o extrato deste jogador")
    return build_player_ledger(db, group=group, player_id=player.id, player_name=player.name, user_id=user_id, serializer=lambda e: _entry_to_out(e, current_user_id=current_user_id))


@group_finance.get("/transactions")
def group_finance_transactions(
    group_id: str,
    player_id: Optional[int] = Query(default=None),
    transaction_type: Optional[str] = Query(default=None, alias="type"),
    status: Optional[str] = None,
    match_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    group = _require_group(db, group_id)
    if _ensure_current_month_baseline(db, group):
        db.commit()

    user_id = None
    if player_id is not None:
        _, user_id = _resolve_player_and_user(db, group_id=group_id, player_id=player_id)

    rows = query_group_entries(
        db,
        group_id=group_id,
        user_id=user_id,
        transaction_type=transaction_type.strip().lower() if transaction_type else None,
        status=status.strip().lower() if status else None,
        match_id=match_id,
        date_from=_parse_iso_date(date_from, field_name="date_from"),
        date_to=_parse_iso_date(date_to, field_name="date_to"),
    )
    return [_entry_to_out(e, current_user_id=current_user_id) for e in rows]


@group_finance.post("/transactions/manual", status_code=201)
def group_finance_create_manual_transaction(
    group_id: str,
    payload: ManualTransactionIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    g = _require_group(db, group_id)

    target_user_id = payload.user_id
    if payload.player_id is not None:
        _, target_user_id = _resolve_player_and_user(db, group_id=group_id, player_id=payload.player_id)

    entry = create_manual_transaction(
        db,
        group=g,
        created_by_user_id=current_user_id,
        user_id=target_user_id,
        player_id=payload.player_id,
        match_id=payload.match_id,
        transaction_type=payload.transaction_type or "manual",
        amount=payload.amount,
        due_date=_parse_iso_date(payload.due_date, field_name="due_date"),
        description=payload.description,
        notes=payload.notes,
    )
    db.commit()
    db.refresh(entry)
    result = _entry_to_out(entry, current_user_id=current_user_id)
    log_event(logger, "finance_manual_transaction_created", user_id=current_user_id, group_id=group_id, transaction_id=entry.id, transaction_type=entry.entry_type, amount_cents=entry.amount_cents)
    return result


@group_finance.post("/transactions/{transaction_id}/mark-paid")
def group_finance_mark_paid_advanced(
    group_id: str,
    transaction_id: int,
    payload: MarkPaidIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    entry = mark_entry_paid(
        db,
        group=_require_group(db, group_id),
        transaction_id=transaction_id,
        acting_user_id=current_user_id,
        amount=payload.amount,
        payment_method=payload.payment_method,
        notes=payload.notes,
        mark_as_paid=payload.mark_as_paid,
    )
    db.commit()
    db.refresh(entry)
    result = _entry_to_out(entry, current_user_id=current_user_id)
    log_event(logger, "finance_transaction_paid", user_id=current_user_id, group_id=group_id, transaction_id=entry.id, paid=entry.paid, paid_amount_cents=entry.paid_amount_cents)
    return result




@group_finance.delete("/transactions/{transaction_id}/mark-paid")
def group_finance_unmark_paid_advanced(
    group_id: str,
    transaction_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    entry = mark_entry_paid(
        db,
        group=_require_group(db, group_id),
        transaction_id=transaction_id,
        acting_user_id=current_user_id,
        amount=None,
        payment_method=None,
        notes=None,
        mark_as_paid=False,
    )
    db.commit()
    db.refresh(entry)
    result = _entry_to_out(entry, current_user_id=current_user_id)
    log_event(logger, "finance_transaction_unpaid", user_id=current_user_id, group_id=group_id, transaction_id=entry.id, paid=entry.paid)
    return result


@group_finance.get("/reports")
def group_finance_reports(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    g, _ = get_group_member(db, group_id, current_user_id)
    if _ensure_current_month_baseline(db, g):
        db.commit()
    return build_reports(db, group=g)


@group_finance.get("/debtors")
def group_finance_debtors(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    _require_group(db, group_id)

    today = utc_today()
    rows = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .filter(GroupFinancialEntry.user_id.isnot(None))
        .all()
    )

    acc: Dict[int, Dict[str, Any]] = {}
    for e in rows:
        status = (e.status or "pending").lower()
        if status in ("paid", "cancelled", "forgiven"):
            continue
        uid = int(e.user_id)
        d = acc.setdefault(uid, {
            "user_id": uid,
            "player_id": None,
            "pending": 0,
            "overdue": 0,
            "charges_count": 0,
            "user_name": None,
            "user_avatar_url": None,
        })
        remaining = max(int(e.amount_cents or 0) - int(getattr(e, "paid_amount_cents", None) or 0), 0)
        d["pending"] += remaining
        d["charges_count"] += 1
        if e.due_date and e.due_date <= today:
            d["overdue"] += remaining

        if d["user_name"] is None:
            u = db.query(User).filter(User.id == uid).first()
            d["user_name"] = _user_display_name(u)
        if d["player_id"] is None:
            p = db.query(Player).filter(Player.owner_id == uid).order_by(Player.id.asc()).first()
            d["player_id"] = p.id if p else None

    out = []
    for d in acc.values():
        out.append({
            "user_id": d["user_id"],
            "player_id": d["player_id"],
            "pending_amount": _cents_to_amount(d["pending"]),
            "overdue_amount": _cents_to_amount(d["overdue"]),
            "charges_count": d["charges_count"],
            "user_name": d["user_name"],
            "user_avatar_url": d["user_avatar_url"],
        })
    out.sort(key=lambda x: (x["overdue_amount"], x["pending_amount"]), reverse=True)
    return out


@group_finance.get("/player-status")
def group_finance_player_status(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    group = _require_group(db, group_id)
    today = utc_today()
    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.status == "active")
        .order_by(GroupMember.id.asc())
        .all()
    )
    out = []
    for m in members:
        entries = _ledger_for_user(db, group_id, int(m.user_id))
        summary = _wallet_summary(entries)
        monthly_overdue = any(
            (e.entry_type or "").lower() == "monthly"
            and (e.status or "pending").lower() not in ("paid", "cancelled", "forgiven")
            and e.due_date is not None
            and e.due_date < today
            for e in entries
        )
        player = db.query(Player).filter(Player.id == m.player_id).first()
        out.append({
            "user_id": m.user_id,
            "player_id": m.player_id,
            "player_name": player.name if player else f"Jogador #{m.player_id}",
            "billing_type": m.billing_type,
            "pending_total": summary["pending_total"],
            "monthly_due": summary["monthly_due"],
            "financial_status": "inadimplente" if monthly_overdue else "adimplente",
        })
    return {
        "group_id": group_id,
        "currency": group.currency,
        "items": out,
    }


@router.put("/groups/{group_id}/settings/payment-due-day")
def set_group_payment_due_day(
    group_id: str,
    payload: DueDayIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    return update_group_finance_settings(group_id, FinanceSettingsIn(due_day=payload.due_day), db, current_user_id)


@router.put("/groups/{group_id}/finance/settings")
def update_group_finance_settings(
    group_id: str,
    payload: FinanceSettingsIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    group, _ = require_group_admin(db, group_id, current_user_id)
    if payload.payment_method is not None:
        group.payment_method = payload.payment_method.strip() or None
    if payload.payment_key is not None:
        group.payment_key = payload.payment_key.strip() or None
    if payload.due_day is not None:
        if not _is_hybrid_group_type(group.group_type):
            raise HTTPException(status_code=400, detail="Data limite só pode ser configurada em grupos híbridos")
        group.payment_due_day = int(payload.due_day)
    db.add(group)
    db.commit()
    db.refresh(group)
    return {
        "group_id": group.id,
        "payment_method": group.payment_method,
        "payment_key": group.payment_key,
        "due_day": group.payment_due_day,
        "message": "Configuração financeira atualizada com sucesso",
    }


@group_finance.get("/automation/status", response_model=FinanceAutomationStatusOut)
def group_finance_automation_status(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    group = _require_group(db, group_id)
    automation = _ensure_finance_automation(db, group)
    if automation["created"]:
        db.commit()
    today = utc_today()
    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.status == "active")
        .all()
    )
    monthly_members = [m for m in members if (m.billing_type or "single").lower() == "monthly"]
    entries = [
        e for e in list_group_entries_ordered(db, group_id)
        if (e.entry_type or "").lower() == "monthly"
        and int(getattr(e, "period_year", 0) or 0) == today.year
        and int(getattr(e, "period_month", 0) or 0) == today.month
    ]
    paid_count = sum(1 for e in entries if (e.status or "pending").lower() == "paid")
    overdue_count = sum(1 for e in entries if _display_status_for_entry(e) == "overdue")
    pending_count = sum(1 for e in entries if _display_status_for_entry(e) == "pending")
    ready = _is_hybrid_group_type(group.group_type) and bool(group.payment_due_day) and float(group.monthly_cost or 0) > 0
    msg = (
        "Automação pronta para gerar e acompanhar as mensalidades do mês atual."
        if ready else
        "Configure mensalidade e dia de vencimento para habilitar a automação financeira."
    )
    return {
        "group_id": group.id,
        "automation_ready": ready,
        "automation_enabled": ready,
        "reference_year": today.year,
        "reference_month": today.month,
        "created_now": automation["created"],
        "skipped_now": automation["skipped"],
        "monthly_members_count": len(monthly_members),
        "generated_entries_count": len(entries),
        "paid_entries_count": paid_count,
        "pending_entries_count": pending_count,
        "overdue_entries_count": overdue_count,
        "due_day": group.payment_due_day,
        "monthly_cost": float(group.monthly_cost or 0),
        "message": msg,
    }


# ---------------------------------------------------------------------
# Existing compatibility endpoints (CORRIGIDO: summary aceita month/year)
# ---------------------------------------------------------------------


@group_finance.get("/summary")
def group_finance_summary(
    group_id: str,
    month: Optional[int] = Query(default=None, ge=1, le=12),
    year: Optional[int] = Query(default=None, ge=2020, le=2099),
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    g, _ = get_group_member(db, group_id, current_user_id)
    baseline_changed = _ensure_current_month_baseline(db, g)
    automation = _ensure_finance_automation(db, g, reference=date(year or utc_today().year, month or utc_today().month, 1))
    if baseline_changed or automation["created"]:
        db.commit()
    return build_summary(db, group=g, current_user_id=current_user_id, year=year, month=month)


@group_finance.get("/entries")
def group_finance_entries(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    group = _require_group(db, group_id)
    baseline_changed = _ensure_current_month_baseline(db, group)
    automation = _ensure_finance_automation(db, group)
    if baseline_changed or automation["created"]:
        db.commit()
    rows = list_group_entries_ordered(db, group_id)
    return [_entry_to_out(e, current_user_id=current_user_id) for e in rows]


@group_finance.post("/entries", status_code=201)
def group_finance_create_entry(
    group_id: str,
    payload: EntryCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    manual = ManualTransactionIn(
        user_id=payload.user_id,
        match_id=payload.match_id,
        transaction_type=payload.entry_type,
        amount=payload.amount,
        due_date=payload.due_date,
        description=payload.description,
        notes=payload.notes,
    )
    return group_finance_create_manual_transaction(group_id, manual, db, current_user_id)


@group_finance.put("/entries/{entry_id}/paid")
def group_finance_mark_paid(
    group_id: str,
    entry_id: int,
    payload: MarkPaidIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    return group_finance_mark_paid_advanced(group_id, entry_id, payload, db, current_user_id)


@group_finance.post("/generate-monthly")
def group_finance_generate_monthly(
    group_id: str,
    payload: GenerateMonthlyIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    g = _require_group(db, group_id)
    now = utc_today()
    result = generate_monthly_entries(
        db,
        group=g,
        year=int(payload.year or now.year),
        month=int(payload.month or now.month),
    )
    db.commit()
    return result



@group_finance.get("/monthly-members", response_model=List[MonthlyMemberOut])
def group_finance_monthly_members(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    g = _require_group(db, group_id)
    today = utc_today()
    year = today.year
    month = today.month
    automation = _ensure_finance_automation(db, g, reference=today)
    if automation["created"]:
        db.commit()
    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.status == "active")
        .order_by(GroupMember.id.asc())
        .all()
    )
    out = []
    for m in members:
        if (m.billing_type or "single").lower() != "monthly":
            continue
        entry = get_monthly_entry(db, group_id=group_id, user_id=int(m.user_id), year=year, month=month)
        player = db.query(Player).filter(Player.id == m.player_id).first()
        confirmer = db.query(User).filter(User.id == entry.confirmed_by_user_id).first() if entry and entry.confirmed_by_user_id else None
        display_status = _display_status_for_entry(entry) if entry else ("overdue" if g.payment_due_day and date(year, month, min(int(g.payment_due_day), monthrange(year, month)[1])) < today else "pending")
        out.append({
            "user_id": int(m.user_id),
            "player_id": int(m.player_id),
            "player_name": player.name if player else f"Jogador #{m.player_id}",
            "billing_type": "monthly",
            "amount": float(g.monthly_cost or 0),
            "paid": bool(entry.paid) if entry else False,
            "status": display_status,
            "display_status": display_status,
            "is_overdue": display_status == "overdue",
            "automation_source": "current_month_auto" if automation["created"] or entry is not None else "manual",
            "due_date": entry.due_date.isoformat() if entry and entry.due_date else None,
            "confirmed_by_user_id": entry.confirmed_by_user_id if entry else None,
            "confirmed_by_user_name": _user_display_name(confirmer),
            "can_unmark": bool(entry and entry.confirmed_by_user_id == current_user_id),
            "entry_id": entry.id if entry else None,
        })
    return out


# =====================================================
# NOVO ENDPOINT: billing-members (mensalistas + avulsos)
# =====================================================


@group_finance.get("/billing-members")
def group_finance_billing_members(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    """
    Retorna todos os membros ativos separados por billing_type:
    - monthly_members: lista de mensalistas com status de pagamento
    - single_members: lista de avulsos com pendências do mês
    """
    require_group_admin(db, group_id, current_user_id)
    g = _require_group(db, group_id)
    baseline_changed = _ensure_current_month_baseline(db, g)
    automation = _ensure_finance_automation(db, g)
    if baseline_changed or automation["created"]:
        db.commit()
    return build_billing_members(db, group=g, current_user_id=current_user_id)


@group_finance.post("/monthly-members/{player_id}/mark-paid")
def group_finance_mark_monthly_member_paid(
    group_id: str,
    player_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    g = _require_group(db, group_id)
    if not g.monthly_cost or float(g.monthly_cost or 0) <= 0:
        raise HTTPException(status_code=400, detail="monthly_cost não configurado")

    player, user_id = _resolve_player_and_user(db, group_id=group_id, player_id=player_id)
    member = db.query(GroupMember).filter(GroupMember.group_id == group_id, GroupMember.player_id == player_id).first()
    if not member or (member.billing_type or "single").lower() != "monthly":
        raise HTTPException(status_code=400, detail="Jogador não é mensalista")

    entry = mark_monthly_member_paid(
        db,
        group=g,
        player_id=player_id,
        user_id=user_id,
        acting_user_id=current_user_id,
        player_name=player.name,
    )
    db.commit()
    db.refresh(entry)
    result = _entry_to_out(entry, current_user_id=current_user_id)
    log_event(logger, "finance_monthly_member_paid", user_id=current_user_id, group_id=group_id, player_id=player_id, transaction_id=entry.id)
    return result


@group_finance.delete("/monthly-members/{player_id}/mark-paid")
def group_finance_unmark_monthly_member_paid(
    group_id: str,
    player_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
):
    require_group_admin(db, group_id, current_user_id)
    _require_group(db, group_id)
    _, user_id = _resolve_player_and_user(db, group_id=group_id, player_id=player_id)
    entry = unmark_monthly_member_paid(
        db,
        group=_require_group(db, group_id),
        player_id=player_id,
        user_id=user_id,
        acting_user_id=current_user_id,
    )
    db.commit()
    db.refresh(entry)
    result = _entry_to_out(entry, current_user_id=current_user_id)
    log_event(logger, "finance_monthly_member_unpaid", user_id=current_user_id, group_id=group_id, player_id=player_id, transaction_id=entry.id)
    return result


# ---------------------------------------------------------------------
# Legacy aliases
# ---------------------------------------------------------------------


@legacy_finance.get("/summary")
def legacy_summary(group_id: str, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    return group_finance_summary(group_id, None, None, db, current_user_id)


@legacy_finance.get("/entries")
def legacy_entries(group_id: str, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    return group_finance_entries(group_id, db, current_user_id)


@legacy_finance.post("/entries", status_code=201)
def legacy_create_entry(payload: EntryCreateIn, group_id: str, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    return group_finance_create_entry(group_id, payload, db, current_user_id)


@legacy_finance.put("/entries/{entry_id}/paid")
def legacy_mark_paid(entry_id: int, payload: MarkPaidIn, group_id: str, db: Session = Depends(get_db), current_user_id: int = Depends(get_current_user_id)):
    return group_finance_mark_paid(group_id, entry_id, payload, db, current_user_id)


router.include_router(group_finance)
router.include_router(legacy_finance)
