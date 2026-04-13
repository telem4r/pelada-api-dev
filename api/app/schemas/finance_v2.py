from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Literal

from pydantic import BaseModel, Field


class FinanceV2SummaryModel(BaseModel):
    group_id: str
    currency: str = 'BRL'

    payment_method: Optional[str] = None
    payment_key: Optional[str] = None
    payment_due_day: Optional[int] = None

    total_paid: float = 0
    total_pending: float = 0
    next_due_date: Optional[str] = None
    cashflow_total: float = 0

    monthly_members_total: float = 0
    single_matches_total: float = 0
    fines_total: float = 0
    venue_total: float = 0
    extra_expenses_total: float = 0
    received_subtotal: float = 0
    expenses_subtotal: float = 0
    month_result: float = 0
    cash_in_box: float = 0
    total_income_paid: float = 0
    total_expense_paid: float = 0
    month_year: Optional[int] = None
    month_month: Optional[int] = None
    snapshot_reference_month: Optional[str] = None
    snapshot_generated_at: Optional[str] = None

    # Backward-compatible V2 fields already used elsewhere.
    balance: float = 0
    received: float = 0
    expenses: float = 0
    open_amount: float = 0
    obligations_count: int = 0
    entries_count: int = 0


class FinanceV2ObligationModel(BaseModel):
    obligation_id: str
    group_id: str
    user_id: Optional[str] = None
    player_id: Optional[str] = None
    match_id: Optional[str] = None
    player_name: Optional[str] = None
    player_avatar_url: Optional[str] = None
    source_type: str
    title: str
    description: Optional[str] = None
    amount: float
    currency: str = 'BRL'
    status: str
    due_date: Optional[date] = None
    competence_month: Optional[int] = None
    competence_year: Optional[int] = None
    created_at: Optional[datetime] = None


class FinanceV2EntryModel(BaseModel):
    entry_id: str
    id: str
    group_id: str
    obligation_id: Optional[str] = None
    user_id: Optional[str] = None
    player_id: Optional[str] = None
    match_id: Optional[str] = None
    player_name: Optional[str] = None
    user_name: Optional[str] = None
    user_avatar_url: Optional[str] = None
    entry_type: str
    type: Optional[str] = None
    category: str
    amount: float
    currency: str = 'BRL'
    status: str = 'paid'
    display_status: Optional[str] = None
    is_overdue: bool = False
    due_date: Optional[str] = None
    description: Optional[str] = None
    paid: bool = True
    paid_at: Optional[datetime] = None
    paid_amount: Optional[float] = None
    payment_method: Optional[str] = None
    notes: Optional[str] = None
    confirmed_by_user_id: Optional[str] = None
    confirmed_by_user_name: Optional[str] = None
    can_unmark: bool = False
    created_at: Optional[datetime] = None


class FinanceV2LedgerModel(BaseModel):
    ledger_id: str
    movement_type: str
    direction: str
    amount: float
    balance_impact: float
    description: str
    reference_date: datetime
    created_at: Optional[datetime] = None


class FinanceV2MonthlyGenerateRequest(BaseModel):
    month: int = Field(..., ge=1, le=12)
    year: int = Field(..., ge=2024, le=2100)


class FinanceV2CreateEntryRequest(BaseModel):
    obligation_id: Optional[str] = None
    entry_type: Literal['inflow', 'outflow']
    category: str = Field(..., min_length=3, max_length=50)
    amount: float = Field(..., gt=0)
    notes: Optional[str] = Field(None, max_length=500)


class FinanceV2GenerateMatchResult(BaseModel):
    match_id: str
    generated_obligations: int = 0
    generated_entries: int = 0


class FinanceV2MonthlyGenerateResult(BaseModel):
    month: int
    year: int
    generated_obligations: int = 0





class FinanceV2ManualTransactionRequest(BaseModel):
    user_id: Optional[str] = None
    player_id: Optional[str] = None
    match_id: Optional[str] = None
    transaction_type: str = Field(default='manual')
    amount: float = Field(..., gt=0)
    due_date: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None

class FinanceV2MarkPaidRequest(BaseModel):
    amount: Optional[float] = Field(None, gt=0)
    payment_method: Optional[str] = Field(None, max_length=80)
    notes: Optional[str] = Field(None, max_length=500)
    mark_as_paid: bool = True


class FinanceV2SettingsRequest(BaseModel):
    payment_method: Optional[str] = Field(None, max_length=80)
    payment_key: Optional[str] = Field(None, max_length=255)
    due_day: Optional[int] = Field(None, ge=1, le=31)


class FinanceV2MonthlyMemberStatusModel(BaseModel):
    user_id: str
    player_id: str
    player_name: str
    avatar_url: Optional[str] = None
    billing_type: str = 'monthly'
    amount: float = 0
    paid: bool = False
    due_date: Optional[str] = None
    confirmed_by_user_id: Optional[str] = None
    confirmed_by_user_name: Optional[str] = None
    can_unmark: bool = False
    entry_id: Optional[str] = None
    status: Optional[str] = None
    display_status: Optional[str] = None
    is_overdue: bool = False
    automation_source: Optional[str] = None


class FinanceV2SingleMemberStatusModel(BaseModel):
    user_id: str
    player_id: str
    player_name: str
    avatar_url: Optional[str] = None
    billing_type: str = 'single'
    month_paid: float = 0
    month_pending: float = 0
    financial_status: str = 'adimplente'


class FinanceV2BillingMembersModel(BaseModel):
    group_id: str
    currency: str = 'BRL'
    year: int
    month: int
    monthly_members: list[FinanceV2MonthlyMemberStatusModel] = []
    single_members: list[FinanceV2SingleMemberStatusModel] = []


class FinanceV2AutomationStatusModel(BaseModel):
    group_id: str
    automation_ready: bool = False
    automation_enabled: bool = False
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
    message: str = ''
