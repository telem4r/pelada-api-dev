from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GroupFinanceSettingsUpdate(BaseModel):
    payment_method: Optional[str] = None
    payment_key: Optional[str] = None
    due_day: Optional[int] = None


class GroupCreateV2Request(BaseModel):
    name: str = Field(..., min_length=3, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    group_type: str = Field(..., pattern='^(avulso|hibrido)$')
    currency: Optional[str] = Field(None, min_length=2, max_length=10)
    country: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, max_length=120)
    modality: Optional[str] = Field(None, max_length=50)
    gender_type: Optional[str] = Field(None, max_length=50)
    payment_method: Optional[str] = Field(None, max_length=50)
    payment_key: Optional[str] = Field(None, max_length=255)
    venue_cost: Optional[float] = None
    per_person_cost: Optional[float] = None
    monthly_cost: Optional[float] = None
    single_cost: Optional[float] = None
    single_waitlist_release_days: Optional[int] = Field(None, ge=0, le=30)
    payment_due_day: Optional[int] = Field(None, ge=1, le=31)
    fine_enabled: Optional[bool] = False
    fine_amount: Optional[float] = None
    fine_reason: Optional[str] = Field(None, max_length=255)
    is_public: Optional[bool] = False


class GroupUpdateV2Request(BaseModel):
    name: Optional[str] = Field(None, min_length=3, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    group_type: Optional[str] = Field(None, pattern='^(avulso|hibrido)$')
    currency: Optional[str] = Field(None, min_length=2, max_length=10)
    country: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    city: Optional[str] = Field(None, max_length=120)
    modality: Optional[str] = Field(None, max_length=50)
    gender_type: Optional[str] = Field(None, max_length=50)
    payment_method: Optional[str] = Field(None, max_length=50)
    payment_key: Optional[str] = Field(None, max_length=255)
    venue_cost: Optional[float] = None
    per_person_cost: Optional[float] = None
    monthly_cost: Optional[float] = None
    single_cost: Optional[float] = None
    single_waitlist_release_days: Optional[int] = Field(None, ge=0, le=30)
    payment_due_day: Optional[int] = Field(None, ge=1, le=31)
    fine_enabled: Optional[bool] = None
    fine_amount: Optional[float] = None
    fine_reason: Optional[str] = Field(None, max_length=255)
    is_public: Optional[bool] = None


class GroupSummaryV2Model(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    currency: str = 'BRL'
    avatar_url: Optional[str] = None
    group_type: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    modality: Optional[str] = None
    gender_type: Optional[str] = None
    payment_method: Optional[str] = None
    payment_key: Optional[str] = None
    venue_cost: Optional[float] = None
    per_person_cost: Optional[float] = None
    monthly_cost: Optional[float] = None
    single_cost: Optional[float] = None
    single_waitlist_release_days: int = 0
    payment_due_day: Optional[int] = None
    fine_enabled: bool = False
    fine_amount: Optional[float] = None
    fine_reason: Optional[str] = None
    is_public: bool = False
    members_count: int = 0
    owner_user_id: str
    owner_name: Optional[str] = None
    is_owner: bool = False
    is_admin: bool = False
    join_request_status: str = 'none'
    is_active: bool = True


class GroupMemberSummaryV2Model(BaseModel):
    user_id: str
    player_id: str
    role: str
    status: str
    billing_type: Optional[str] = None
    skill_rating: Optional[int] = None
    financial_status: Optional[str] = None
    profile: dict
    player: dict


class GroupJoinRequestV2Model(BaseModel):
    request_id: str
    user_id: str
    player_id: str
    status: str
    role: str = 'member'
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    billing_type: Optional[str] = None
    skill_rating: Optional[int] = None


class GroupMemberRoleUpdateV2Request(BaseModel):
    role: str = Field(..., pattern='^(admin|member)$')


class GroupMemberBillingUpdateV2Request(BaseModel):
    billing_type: str = Field(..., pattern='^(mensalista|avulso)$')


class GroupInvitationCreateV2Request(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)


class GroupInvitationV2Model(BaseModel):
    invitation_id: str
    group_id: str
    invited_email: str
    status: str
    token: str
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
