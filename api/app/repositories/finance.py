from __future__ import annotations

from datetime import date, datetime, time
from sqlalchemy.orm import Session

from app.models import GroupFinancialEntry, GroupFinancialMonthlySnapshot, GroupMemberFinancialStatus


def list_group_entries(db: Session, group_id: str):
    return db.query(GroupFinancialEntry).filter(GroupFinancialEntry.group_id == group_id).all()


def list_group_entries_ordered(db: Session, group_id: str):
    return (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .order_by(GroupFinancialEntry.created_at.desc(), GroupFinancialEntry.id.desc())
        .all()
    )


def list_user_entries(db: Session, group_id: str, user_id: int):
    return (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id, GroupFinancialEntry.user_id == user_id)
        .order_by(GroupFinancialEntry.due_date.desc().nullslast(), GroupFinancialEntry.created_at.desc())
        .all()
    )


def get_entry(db: Session, *, group_id: str, entry_id: int):
    return (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id, GroupFinancialEntry.id == entry_id)
        .first()
    )


def get_monthly_entry(db: Session, *, group_id: str, user_id: int, year: int, month: int):
    return (
        db.query(GroupFinancialEntry)
        .filter(
            GroupFinancialEntry.group_id == group_id,
            GroupFinancialEntry.user_id == user_id,
            GroupFinancialEntry.entry_type == "monthly",
            GroupFinancialEntry.period_year == year,
            GroupFinancialEntry.period_month == month,
        )
        .first()
    )


def query_group_entries(
    db: Session,
    *,
    group_id: str,
    user_id: int | None = None,
    transaction_type: str | None = None,
    status: str | None = None,
    match_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    q = db.query(GroupFinancialEntry).filter(GroupFinancialEntry.group_id == group_id)
    if user_id is not None:
        q = q.filter(GroupFinancialEntry.user_id == user_id)
    if transaction_type:
        q = q.filter(GroupFinancialEntry.entry_type == transaction_type)
    if status:
        q = q.filter(GroupFinancialEntry.status == status)
    if match_id is not None:
        q = q.filter(GroupFinancialEntry.match_id == match_id)
    if date_from is not None:
        q = q.filter(GroupFinancialEntry.created_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        q = q.filter(GroupFinancialEntry.created_at <= datetime.combine(date_to, time.max))
    return q.order_by(GroupFinancialEntry.created_at.desc(), GroupFinancialEntry.id.desc()).all()


def get_snapshot(db: Session, group_id: str, reference_month: date):
    return db.query(GroupFinancialMonthlySnapshot).filter(
        GroupFinancialMonthlySnapshot.group_id == group_id,
        GroupFinancialMonthlySnapshot.reference_month == reference_month,
    ).first()


def upsert_snapshot(db: Session, snapshot: GroupFinancialMonthlySnapshot) -> GroupFinancialMonthlySnapshot:
    db.add(snapshot)
    db.flush()
    return snapshot


def list_member_financial_statuses(db: Session, group_id: str, reference_month: date):
    return (
        db.query(GroupMemberFinancialStatus)
        .filter(
            GroupMemberFinancialStatus.group_id == group_id,
            GroupMemberFinancialStatus.reference_month == reference_month,
        )
        .all()
    )


def get_member_financial_status(db: Session, group_id: str, player_id: int, reference_month: date):
    return db.query(GroupMemberFinancialStatus).filter(
        GroupMemberFinancialStatus.group_id == group_id,
        GroupMemberFinancialStatus.player_id == player_id,
        GroupMemberFinancialStatus.reference_month == reference_month,
    ).first()
