from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.finance_v2 import (
    FinanceV2AutomationStatusModel,
    FinanceV2BillingMembersModel,
    FinanceV2CreateEntryRequest,
    FinanceV2EntryModel,
    FinanceV2GenerateMatchResult,
    FinanceV2LedgerModel,
    FinanceV2ManualTransactionRequest,
    FinanceV2MarkPaidRequest,
    FinanceV2MonthlyGenerateRequest,
    FinanceV2MonthlyGenerateResult,
    FinanceV2MonthlyMemberStatusModel,
    FinanceV2ObligationModel,
    FinanceV2SettingsRequest,
    FinanceV2SummaryModel,
)
from app.services.finance_v2_service import FinanceV2Service

router = APIRouter(prefix='/v2/groups/{group_id}/finance', tags=['Financeiro V2'])
quick_router = APIRouter(prefix='/v2/groups/finance', tags=['Financeiro V2'])
service = FinanceV2Service()


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get('/summary', response_model=FinanceV2SummaryModel)
def get_summary(
    group_id: str,
    year: int | None = Query(default=None, ge=2024, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.get_summary(db, principal, group_id, year=year, month=month)


@router.get('/obligations', response_model=list[FinanceV2ObligationModel])
def list_obligations(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_obligations(db, principal, group_id)


@router.get('/entries', response_model=list[FinanceV2EntryModel])
def list_entries(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_entries(db, principal, group_id)


@router.get('/ledger', response_model=list[FinanceV2LedgerModel])
def list_ledger(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_ledger(db, principal, group_id)


@router.get('/monthly-members', response_model=list[FinanceV2MonthlyMemberStatusModel])
def list_monthly_members(
    group_id: str,
    year: int | None = Query(default=None, ge=2024, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.list_monthly_members(db, principal, group_id, year=year, month=month)


@router.get('/billing-members', response_model=FinanceV2BillingMembersModel)
def get_billing_members(
    group_id: str,
    year: int | None = Query(default=None, ge=2024, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.get_billing_members(db, principal, group_id, year=year, month=month)


@router.get('/automation/status', response_model=FinanceV2AutomationStatusModel)
def get_automation_status(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_automation_status(db, principal, group_id)


@router.put('/settings')
def update_settings(group_id: str, payload: FinanceV2SettingsRequest, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_settings(db, principal, group_id, payload)


@router.post('/obligations/generate-monthly', response_model=FinanceV2MonthlyGenerateResult)
def generate_monthly(group_id: str, payload: FinanceV2MonthlyGenerateRequest, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.generate_monthly_obligations(db, principal, group_id, payload)


@router.post('/obligations/generate-match/{match_id}', response_model=FinanceV2GenerateMatchResult)
def generate_match(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.generate_match_obligations(db, principal, group_id, match_id)


@router.post('/entries', response_model=FinanceV2EntryModel)
def create_entry(group_id: str, payload: FinanceV2CreateEntryRequest, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.create_entry(db, principal, group_id, payload)


@router.post('/transactions/manual', response_model=FinanceV2EntryModel, status_code=201)
def create_manual_transaction(group_id: str, payload: FinanceV2ManualTransactionRequest, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.create_manual_transaction(db, principal, group_id, payload)


@router.post('/entries/{reference_id}/paid', response_model=FinanceV2EntryModel)
def mark_paid(group_id: str, reference_id: str, payload: FinanceV2MarkPaidRequest, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_paid(db, principal, group_id, reference_id, payload)


@router.delete('/entries/{entry_id}/paid')
def unmark_paid(group_id: str, entry_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.unmark_paid(db, principal, group_id, entry_id)


@router.post('/monthly-members/{player_id}/mark-paid', response_model=FinanceV2EntryModel)
def mark_monthly_member_paid(
    group_id: str,
    player_id: str,
    year: int | None = Query(default=None, ge=2024, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.mark_monthly_member_paid(db, principal, group_id, player_id, year=year, month=month)


@router.delete('/monthly-members/{player_id}/mark-paid')
def unmark_monthly_member_paid(
    group_id: str,
    player_id: str,
    year: int | None = Query(default=None, ge=2024, le=2100),
    month: int | None = Query(default=None, ge=1, le=12),
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.unmark_monthly_member_paid(db, principal, group_id, player_id, year=year, month=month)


@router.delete('/entries/{entry_id}', status_code=204)
def delete_entry(group_id: str, entry_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    service.delete_entry(db, principal, group_id, entry_id)
    return None


@router.delete('/obligations/{obligation_id}')
def delete_obligation(group_id: str, obligation_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.soft_delete_obligation(db, principal, group_id, obligation_id)


@router.get('/obligations/deleted', response_model=list)
def list_deleted_obligations(group_id: str, year: int | None = Query(default=None, ge=2024, le=2100), month: int | None = Query(default=None, ge=1, le=12), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_deleted_obligations(db, principal, group_id, year=year, month=month)


@quick_router.get('/quick-access')
def list_quick_access_groups(
    principal: SupabasePrincipal = Depends(get_current_supabase_principal),
    db: Session = Depends(get_db_session),
):
    return service.get_quick_access_groups(db, principal)
