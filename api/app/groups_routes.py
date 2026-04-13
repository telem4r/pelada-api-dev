from __future__ import annotations
from app.core.logging import configure_logging, log_event
from app.core.audit import audit_admin_action
logger = configure_logging()
from datetime import datetime, date
import calendar
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, AliasChoices, ConfigDict, model_validator
from sqlalchemy import or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.security import get_current_user  # retorna user_id (int)
from app.models import Group, GroupMember, GroupJoinRequest, GroupFinancialEntry, User, Match, MatchEvent, MatchParticipant, MatchDrawTeam, Player
from app.permissions import get_group_member, get_user_primary_player, require_group_admin_or_owner
from app.avatars_routes import resolve_avatar_url
from app.communication_utils import create_notification, notification_allowed
from app.core.time import utc_now
from app.services.membership_service import (
    approve_join_request_service,
    get_my_membership as get_my_membership_service,
    leave_group as leave_group_service,
    list_join_requests_service,
    list_members as list_members_service,
    reject_join_request_service,
    remove_member as remove_member_service,
    request_join_group as request_join_group_service,
    set_member_billing_type_service,
    set_member_role as set_member_role_service,
    set_member_skill_rating_service,
)

router = APIRouter(prefix="/groups", tags=["groups"])


def _norm_text(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v if v else None


def _norm_role(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _normalize_group_type(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    norm = (
        raw.replace("í", "i")
        .replace("é", "e")
        .replace("á", "a")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("â", "a")
        .replace("ê", "e")
        .replace("ô", "o")
        .replace("ã", "a")
        .replace("õ", "o")
    )
    if "hibrid" in norm or "hybrid" in norm:
        return "hibrido"
    if "avuls" in norm or norm in {"single", "casual"}:
        return "avulso"
    return norm


def _is_hybrid_group_type(value: Optional[str]) -> bool:
    return _normalize_group_type(value) == "hibrido"


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=120)
    currency: str = Field(..., min_length=2, max_length=10)
    avatar_url: Optional[str] = Field(default=None, validation_alias=AliasChoices("avatar_url", "avatarUrl"))

    country: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    city: str = Field(..., min_length=1, max_length=120)

    modality: str = Field(..., min_length=1, max_length=50)
    group_type: str = Field(..., validation_alias=AliasChoices("group_type", "groupType"))
    gender_type: str = Field(..., validation_alias=AliasChoices("gender_type", "genderType"))

    payment_method: Optional[str] = Field(default=None, validation_alias=AliasChoices("payment_method", "paymentMethod"))
    payment_key: Optional[str] = Field(default=None, validation_alias=AliasChoices("payment_key", "paymentKey"))

    venue_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("venue_cost", "venueCost"))
    per_person_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("per_person_cost", "perPersonCost"))
    monthly_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("monthly_cost", "monthlyCost"))
    single_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("single_cost", "singleCost"))
    single_waitlist_release_days: Optional[int] = Field(default=0, validation_alias=AliasChoices("single_waitlist_release_days", "singleWaitlistReleaseDays"))

    # Financeiro (mensalistas): dia limite de pagamento (1-31)
    payment_due_day: Optional[int] = Field(default=None, validation_alias=AliasChoices("payment_due_day", "paymentDueDay"))

    fine_enabled: bool = Field(default=False, validation_alias=AliasChoices("fine_enabled", "fineEnabled"))
    fine_amount: Optional[float] = Field(default=None, validation_alias=AliasChoices("fine_amount", "fineAmount"))
    fine_reason: Optional[str] = Field(default=None, validation_alias=AliasChoices("fine_reason", "fineReason"))

    is_public: bool = Field(default=False, validation_alias=AliasChoices("is_public", "isPublic"))

    @model_validator(mode="after")
    def _normalize(self):
        self.name = (self.name or "").strip()
        self.currency = (self.currency or "").strip().upper()
        self.avatar_url = _norm_text(self.avatar_url)

        self.country = (self.country or "").strip()
        self.state = (self.state or "").strip()
        self.city = (self.city or "").strip()

        self.modality = (self.modality or "").strip()
        self.group_type = (self.group_type or "").strip()
        self.gender_type = (self.gender_type or "").strip()

        self.payment_method = _norm_text(self.payment_method)
        self.payment_key = _norm_text(self.payment_key)
        self.fine_reason = _norm_text(self.fine_reason)

        if self.single_waitlist_release_days is not None:
            if int(self.single_waitlist_release_days) < 0:
                raise ValueError("single_waitlist_release_days deve ser >= 0")
            self.single_waitlist_release_days = int(self.single_waitlist_release_days)

        if self.payment_due_day is not None:
            if not (1 <= int(self.payment_due_day) <= 31):
                raise ValueError("payment_due_day deve estar entre 1 e 31")
            self.payment_due_day = int(self.payment_due_day)
        return self


class GroupUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    currency: Optional[str] = Field(default=None, min_length=2, max_length=10)
    avatar_url: Optional[str] = Field(default=None, validation_alias=AliasChoices("avatar_url", "avatarUrl"))

    country: Optional[str] = Field(default=None, min_length=1, max_length=100)
    state: Optional[str] = Field(default=None, min_length=1, max_length=100)
    city: Optional[str] = Field(default=None, min_length=1, max_length=120)

    modality: Optional[str] = Field(default=None, min_length=1, max_length=50)
    group_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("group_type", "groupType"))
    gender_type: Optional[str] = Field(default=None, validation_alias=AliasChoices("gender_type", "genderType"))

    payment_method: Optional[str] = Field(default=None, validation_alias=AliasChoices("payment_method", "paymentMethod"))
    payment_key: Optional[str] = Field(default=None, validation_alias=AliasChoices("payment_key", "paymentKey"))

    venue_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("venue_cost", "venueCost"))
    per_person_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("per_person_cost", "perPersonCost"))
    monthly_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("monthly_cost", "monthlyCost"))
    single_cost: Optional[float] = Field(default=None, validation_alias=AliasChoices("single_cost", "singleCost"))
    single_waitlist_release_days: Optional[int] = Field(default=0, validation_alias=AliasChoices("single_waitlist_release_days", "singleWaitlistReleaseDays"))

    # Financeiro (mensalistas): dia limite de pagamento (1-31)
    payment_due_day: Optional[int] = Field(default=None, validation_alias=AliasChoices("payment_due_day", "paymentDueDay"))

    fine_enabled: Optional[bool] = Field(default=None, validation_alias=AliasChoices("fine_enabled", "fineEnabled"))
    fine_amount: Optional[float] = Field(default=None, validation_alias=AliasChoices("fine_amount", "fineAmount"))
    fine_reason: Optional[str] = Field(default=None, validation_alias=AliasChoices("fine_reason", "fineReason"))

    is_public: Optional[bool] = Field(default=None, validation_alias=AliasChoices("is_public", "isPublic"))

    @model_validator(mode="after")
    def _normalize(self):
        if self.name is not None:
            self.name = self.name.strip()

        if self.currency is not None:
            self.currency = self.currency.strip().upper()
        if self.avatar_url is not None:
            self.avatar_url = _norm_text(self.avatar_url)

        if self.country is not None:
            self.country = self.country.strip()
        if self.state is not None:
            self.state = self.state.strip()
        if self.city is not None:
            self.city = self.city.strip()

        if self.modality is not None:
            self.modality = self.modality.strip()
        if self.group_type is not None:
            self.group_type = self.group_type.strip()
        if self.gender_type is not None:
            self.gender_type = self.gender_type.strip()

        if self.payment_method is not None:
            self.payment_method = _norm_text(self.payment_method)
        if self.payment_key is not None:
            self.payment_key = _norm_text(self.payment_key)

        if self.fine_reason is not None:
            self.fine_reason = _norm_text(self.fine_reason)

        if self.single_waitlist_release_days is not None:
            if int(self.single_waitlist_release_days) < 0:
                raise ValueError("single_waitlist_release_days deve ser >= 0")
            self.single_waitlist_release_days = int(self.single_waitlist_release_days)

        if self.payment_due_day is not None:
            if not (1 <= int(self.payment_due_day) <= 31):
                raise ValueError("payment_due_day deve estar entre 1 e 31")
            self.payment_due_day = int(self.payment_due_day)

        return self


class GroupOut(BaseModel):
    id: str
    owner_id: int
    name: str
    currency: str
    avatar_url: Optional[str] = None
    country: str
    state: str
    city: str
    modality: str
    group_type: str
    gender_type: str

    payment_method: Optional[str] = None
    payment_key: Optional[str] = None

    venue_cost: Optional[float] = None
    per_person_cost: Optional[float] = None
    monthly_cost: Optional[float] = None
    single_cost: Optional[float] = None
    single_waitlist_release_days: Optional[int] = None

    payment_due_day: Optional[int] = None

    fine_enabled: bool
    fine_amount: Optional[float] = None
    fine_reason: Optional[str] = None

    is_public: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


def _require_membership(db: Session, group_id: str, user_id: int) -> GroupMember:
    _, m = get_group_member(db, group_id=group_id, user_id=user_id)
    return m


def _require_admin_or_owner(member: GroupMember):
    if _norm_role(member.role) not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Sem permissão")


def _require_owner(member: GroupMember):
    if _norm_role(member.role) != "owner":
        raise HTTPException(status_code=403, detail="Apenas o owner pode apagar o grupo")


@router.post("", response_model=GroupOut)
def create_group(
    payload: GroupCreate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """
    Regra:
    - quem cria o grupo vira DONO (owner_id)
    - e também vira membro ACTIVE com role=owner (group_members.user_id)
    """
    try:
        dup = (
            db.query(Group)
            .filter(Group.owner_id == current_user_id)
            .filter(Group.name == payload.name)
            .first()
        )
        if dup:
            raise HTTPException(status_code=400, detail="Você já possui um grupo com esse nome")

        group = Group(
            owner_id=current_user_id,
            name=payload.name,
            currency=payload.currency,
            avatar_url=payload.avatar_url,
            country=payload.country,
            state=payload.state,
            city=payload.city,
            modality=payload.modality,
            group_type=payload.group_type,
            gender_type=payload.gender_type,
            payment_method=payload.payment_method,
            payment_key=payload.payment_key,
            venue_cost=payload.venue_cost,
            per_person_cost=payload.per_person_cost,
            monthly_cost=payload.monthly_cost,
            single_cost=payload.single_cost,
            single_waitlist_release_days=int(payload.single_waitlist_release_days or 0),
            payment_due_day=payload.payment_due_day,
            fine_enabled=payload.fine_enabled,
            fine_amount=payload.fine_amount,
            fine_reason=payload.fine_reason,
            is_public=payload.is_public,
        )
        db.add(group)
        db.flush()  # garante group.id

        owner_player = get_user_primary_player(db, current_user_id)

        owner_member = GroupMember(
            group_id=group.id,
            user_id=current_user_id,  # compat
            player_id=owner_player.id,
            role="owner",
            status="active",
        )
        db.add(owner_member)

        db.commit()
        db.refresh(group)
        group.avatar_url = resolve_avatar_url(group.avatar_url)
        return group

    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[GroupOut])
def list_groups(
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """
    Visibilidade:
    - usuário só lista grupos onde ele é membro ativo (member/admin/owner)
    """
    query = (
        db.query(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .filter(GroupMember.player_id == get_user_primary_player(db, current_user_id).id)
        .filter(GroupMember.status == "active")
    )

    if q:
        qq = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Group.name.ilike(qq),
                Group.city.ilike(qq),
                Group.state.ilike(qq),
                Group.country.ilike(qq),
            )
        )

    groups = query.order_by(Group.created_at.desc()).all()
    for g in groups:
        g.avatar_url = resolve_avatar_url(getattr(g, "avatar_url", None))
    return groups


@router.get("/{group_id}", response_model=GroupOut)
def get_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    me = _require_membership(db, group_id, current_user_id)
    can_view_skill = (getattr(me, 'role', '') in ('owner','admin'))

    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    group.avatar_url = resolve_avatar_url(getattr(group, "avatar_url", None))
    return group


@router.put("/{group_id}", response_model=GroupOut)
def update_group(
    group_id: str,
    payload: GroupUpdate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    member = _require_membership(db, group_id, current_user_id)
    _require_admin_or_owner(member)

    data = payload.model_dump(exclude_unset=True)
    data.pop("owner_id", None)
    data.pop("id", None)

    if not data:
        return group

    for k, v in data.items():
        setattr(group, k, v)

    db.add(group)
    db.commit()
    db.refresh(group)
    group.avatar_url = resolve_avatar_url(getattr(group, "avatar_url", None))
    return group


@router.delete("/{group_id}")
def delete_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    member = _require_membership(db, group_id, current_user_id)
    _require_owner(member)

    try:
        # Compatibilidade com bases antigas onde payments.owner_id ainda não existe.
        # Evita que o SQLAlchemy tente carregar Payment com coluna inexistente ao apagar o grupo.
        db.execute(text("DELETE FROM payments WHERE group_id = :group_id"), {"group_id": group_id})
        db.delete(group)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir grupo: {e}")

    return {"ok": True}


# =====================================================
# MEMBERS (owner/admin/member)
# =====================================================

class MemberProfileOut(BaseModel):
    """Dados do perfil do utilizador (User)."""

    name: Optional[str] = None
    avatar_url: Optional[str] = None


class MemberPlayerOut(BaseModel):
    """Dados do jogador (Player) + campos do utilizador necessários ao card."""

    birth_date: Optional[date] = None
    position: Optional[str] = None
    preferred_foot: Optional[str] = None


class GroupMemberOut(BaseModel):
    id: int
    group_id: str
    user_id: int
    player_id: int
    role: str
    status: str

    billing_type: Optional[str] = None
    skill_rating: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    # ✅ enriquecido para o Flutter renderizar a lista de membros
    profile: Optional[MemberProfileOut] = None
    player: Optional[MemberPlayerOut] = None

    class Config:
        from_attributes = True


class GroupMemberRoleUpdate(BaseModel):
    role: str = Field(..., description="admin ou member")

    @model_validator(mode="after")
    def _normalize(self):
        self.role = (self.role or "").strip().lower()
        return self


class GroupJoinRequestOut(BaseModel):
    id: int
    group_id: str
    user_id: int
    player_id: int
    status: str
    role: Optional[str] = None
    player_name: Optional[str] = None
    avatar_url: Optional[str] = None
    profile: Optional[MemberProfileOut] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/{group_id}/members", response_model=List[GroupMemberOut])
def list_group_members(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    return list_members_service(db, group_id, current_user_id)


@router.put("/{group_id}/members/{member_user_id}/role", response_model=GroupMemberOut)
def set_member_role(
    group_id: str,
    member_user_id: int,
    payload: GroupMemberRoleUpdate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = set_member_role_service(db, group_id, member_user_id, payload.role, current_user_id)
    audit_admin_action(logger, action="set_member_role", actor_user_id=current_user_id, group_id=group_id, target_user_id=member_user_id, role=result.get("role"))
    return result


@router.delete("/{group_id}/members/{member_user_id}")
def remove_member(
    group_id: str,
    member_user_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = remove_member_service(db, group_id, member_user_id, current_user_id)
    audit_admin_action(logger, action="remove_member", actor_user_id=current_user_id, group_id=group_id, target_user_id=member_user_id)
    return result




@router.get("/{group_id}/members/me")
def get_my_group_membership(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    return get_my_membership_service(db, group_id, current_user_id)


@router.delete("/{group_id}/leave")
def leave_group_alias(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = leave_group_service(db, group_id, current_user_id)
    log_event(logger, "group_leave_requested", user_id=current_user_id, group_id=group_id, via="alias")
    return result


@router.delete("/{group_id}/members/me")
def leave_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = leave_group_service(db, group_id, current_user_id)
    log_event(logger, "group_leave_requested", user_id=current_user_id, group_id=group_id, via="members_me")
    return result


@router.post("/{group_id}/join-requests", response_model=GroupJoinRequestOut)
def request_join_group(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = request_join_group_service(db, group_id, current_user_id)
    log_event(logger, "group_join_request_created", user_id=current_user_id, group_id=group_id, request_id=getattr(result, "id", None))
    return result


@router.get("/{group_id}/join-requests", response_model=List[GroupJoinRequestOut])
def list_join_requests(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    return list_join_requests_service(db, group_id, current_user_id)


@router.post("/{group_id}/join-requests/{request_id}/approve")
def approve_join_request(
    group_id: str,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = approve_join_request_service(db, group_id, request_id, current_user_id)
    audit_admin_action(logger, action="approve_group_join_request", actor_user_id=current_user_id, group_id=group_id, target_request_id=request_id)
    return result


@router.post("/{group_id}/join-requests/{request_id}/reject")
def reject_join_request(
    group_id: str,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = reject_join_request_service(db, group_id, request_id, current_user_id)
    audit_admin_action(logger, action="reject_group_join_request", actor_user_id=current_user_id, group_id=group_id, target_request_id=request_id)
    return result


# =====================================================
# SKILL RATING (1-5) - usado no sorteio balanceado
# =====================================================

class GroupMemberSkillUpdate(BaseModel):
    skill_rating: int = Field(..., ge=1, le=5, validation_alias=AliasChoices("skill_rating", "skillRating"))

@router.put("/{group_id}/members/{member_user_id}/skill-rating", response_model=GroupMemberOut)
def set_member_skill_rating(
    group_id: str,
    member_user_id: int,
    payload: GroupMemberSkillUpdate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = set_member_skill_rating_service(db, group_id, member_user_id, payload.skill_rating, current_user_id)
    audit_admin_action(logger, action="set_member_skill_rating", actor_user_id=current_user_id, group_id=group_id, target_user_id=member_user_id, skill_rating=payload.skill_rating)
    return result

# =====================================================
# BILLING TYPE (monthly/single) - usado no Financeiro
# =====================================================


class GroupMemberBillingUpdate(BaseModel):
    billing_type: str = Field(..., validation_alias=AliasChoices("billing_type", "billingType"))

    @model_validator(mode="after")
    def _normalize(self):
        self.billing_type = (self.billing_type or "").strip().lower()
        if self.billing_type not in ("monthly", "single"):
            raise ValueError("billing_type deve ser 'monthly' ou 'single'")
        return self


@router.put("/{group_id}/members/{member_user_id}/billing", response_model=GroupMemberOut)
def set_member_billing_type(
    group_id: str,
    member_user_id: int,
    payload: GroupMemberBillingUpdate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = set_member_billing_type_service(db, group_id, member_user_id, payload.billing_type, current_user_id)
    audit_admin_action(logger, action="set_member_billing_type", actor_user_id=current_user_id, group_id=group_id, target_user_id=member_user_id, billing_type=payload.billing_type)
    return result


# =====================================================
# FINANCEIRO DO GRUPO (controle interno)
# =====================================================


def _cents(v: float) -> int:
    try:
        return int(round((v or 0) * 100))
    except Exception:
        return 0


def _money(cents: int) -> float:
    return (cents or 0) / 100.0


class FinancialEntryCreate(BaseModel):
    # para lançamentos individuais, informe user_id; para lançamentos do grupo (ex: custo da quadra), deixe null
    user_id: Optional[int] = Field(default=None, validation_alias=AliasChoices("user_id", "userId"))
    match_id: Optional[int] = Field(default=None, validation_alias=AliasChoices("match_id", "matchId"))

    entry_type: str = Field(..., validation_alias=AliasChoices("entry_type", "entryType"))
    amount: float
    due_date: Optional[date] = Field(default=None, validation_alias=AliasChoices("due_date", "dueDate"))
    description: Optional[str] = None

    @model_validator(mode="after")
    def _normalize(self):
        self.entry_type = (self.entry_type or "").strip().lower()
        if self.entry_type not in ("monthly", "single", "fine", "manual", "venue"):
            raise ValueError("entry_type inválido")
        self.description = _norm_text(self.description)
        return self


class FinancialEntryOut(BaseModel):
    id: int
    group_id: str
    user_id: Optional[int] = None
    match_id: Optional[int] = None
    entry_type: str
    amount: float
    currency: str
    status: str
    due_date: Optional[date] = None
    description: Optional[str] = None
    paid: bool
    paid_at: Optional[datetime] = None
    confirmed_by_user_id: Optional[int] = None

    # para UI
    user_name: Optional[str] = None
    user_avatar_url: Optional[str] = None


class FinancialSummaryOut(BaseModel):
    group_id: str
    currency: str

    payment_method: Optional[str] = None
    payment_key: Optional[str] = None
    payment_due_day: Optional[int] = None

    total_paid: float
    total_pending: float
    next_due_date: Optional[date] = None

    # breakdown
    paid_monthly: float
    paid_single: float
    paid_fine: float
    paid_manual: float
    paid_venue: float

    pending_monthly: float
    pending_single: float
    pending_fine: float
    pending_manual: float
    pending_venue: float

    cashflow_total: float


def _user_display_name(u: Optional[User]) -> Optional[str]:
    if not u:
        return None
    name = ((u.first_name or "").strip() + " " + (u.last_name or "").strip()).strip()
    if name:
        return name
    return (u.name or "").strip() or None


def _entry_to_out(db: Session, e: GroupFinancialEntry) -> FinancialEntryOut:
    u = None
    if e.user_id is not None:
        u = db.query(User).filter(User.id == e.user_id).first()

    return FinancialEntryOut(
        id=e.id,
        group_id=e.group_id,
        user_id=e.user_id,
        match_id=e.match_id,
        entry_type=e.entry_type,
        amount=_money(e.amount_cents),
        currency=e.currency,
        status=e.status,
        due_date=e.due_date,
        description=e.description,
        paid=bool(e.paid),
        paid_at=e.paid_at,
        confirmed_by_user_id=e.confirmed_by_user_id,
        user_name=_user_display_name(u),
        user_avatar_url=(u.avatar_url if u else None),
    )


@router.get("/{group_id}/finance/summary", response_model=FinancialSummaryOut)
def finance_summary(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Resumo financeiro do grupo (apenas ADM/Owner)."""
    try:
        group, _ = require_group_admin_or_owner(db, group_id, current_user_id)

        entries = db.query(GroupFinancialEntry).filter(GroupFinancialEntry.group_id == group_id).all()

        def _amt(e: GroupFinancialEntry) -> int:
            # Blindagem contra dados legados com amount_cents NULL
            return int(e.amount_cents or 0)

        def _sum(kind: str, paid: bool) -> int:
            return sum(_amt(e) for e in entries if (e.entry_type == kind) and (bool(e.paid) == paid))

        paid_total = sum(_amt(e) for e in entries if bool(e.paid))
        pending_total = sum(_amt(e) for e in entries if not bool(e.paid))

        # Próximo vencimento: menor due_date pendente
        due_dates = [e.due_date for e in entries if (not bool(e.paid)) and e.due_date is not None]
        next_due = min(due_dates) if due_dates else None

        paid_monthly = _sum("monthly", True)
        paid_single = _sum("single", True)
        paid_fine = _sum("fine", True)
        paid_manual = _sum("manual", True)
        paid_venue = _sum("venue", True)

        pending_monthly = _sum("monthly", False)
        pending_single = _sum("single", False)
        pending_fine = _sum("fine", False)
        pending_manual = _sum("manual", False)
        pending_venue = _sum("venue", False)

        # Fluxo de caixa acumulativo: receitas pagas - despesas pagas
        cashflow = paid_total

        return FinancialSummaryOut(
            group_id=group_id,
            currency=(group.currency or "BRL"),
            payment_method=group.payment_method,
            payment_key=group.payment_key,
            payment_due_day=getattr(group, "payment_due_day", None),
            total_paid=_money(paid_total),
            total_pending=_money(pending_total),
            next_due_date=next_due,
            paid_monthly=_money(paid_monthly),
            paid_single=_money(paid_single),
            paid_fine=_money(paid_fine),
            paid_manual=_money(paid_manual),
            paid_venue=_money(paid_venue),
            pending_monthly=_money(pending_monthly),
            pending_single=_money(pending_single),
            pending_fine=_money(pending_fine),
            pending_manual=_money(pending_manual),
            pending_venue=_money(pending_venue),
            cashflow_total=_money(cashflow),
        )
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unhandled: {str(e)}")


@router.get("/{group_id}/finance/entries", response_model=List[FinancialEntryOut])
def list_financial_entries(
    group_id: str,
    user_id: Optional[int] = None,
    status: Optional[str] = None,
    entry_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Lista lançamentos financeiros.

    - ADM/Owner: pode listar tudo (ou filtrar por user_id)
    - Membro: use /finance/me
    """
    require_group_admin_or_owner(db, group_id, current_user_id)

    q = db.query(GroupFinancialEntry).filter(GroupFinancialEntry.group_id == group_id)
    if user_id is not None:
        q = q.filter(GroupFinancialEntry.user_id == user_id)
    if status is not None:
        st = status.strip().lower()
        if st in ("pending", "paid"):
            q = q.filter(GroupFinancialEntry.status == st)
    if entry_type is not None:
        et = entry_type.strip().lower()
        q = q.filter(GroupFinancialEntry.entry_type == et)

    entries = q.order_by(GroupFinancialEntry.created_at.desc()).all()
    return [_entry_to_out(db, e) for e in entries]


@router.get("/{group_id}/finance/me", response_model=List[FinancialEntryOut])
def list_my_financial_entries(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Membro vê somente sua posição financeira dentro do grupo."""
    _require_membership(db, group_id, current_user_id)

    entries = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .filter(GroupFinancialEntry.user_id == current_user_id)
        .order_by(GroupFinancialEntry.created_at.desc())
        .all()
    )

    return [_entry_to_out(db, e) for e in entries]


@router.post("/{group_id}/finance/entries", response_model=FinancialEntryOut)
def create_financial_entry(
    group_id: str,
    payload: FinancialEntryCreate,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Cria um lançamento financeiro (ADM/Owner)."""
    group, _ = require_group_admin_or_owner(db, group_id, current_user_id)

    # Se entrada for "venue", representamos como despesa negativa
    amount_cents = _cents(payload.amount)
    if payload.entry_type == "venue" and amount_cents > 0:
        amount_cents = -abs(amount_cents)

    py = None
    pm = None
    if payload.entry_type == "monthly":
        ref = payload.due_date or date.today()
        py = int(ref.year)
        pm = int(ref.month)

    entry = GroupFinancialEntry(
        group_id=group_id,
        user_id=payload.user_id,
        match_id=payload.match_id,
        entry_type=payload.entry_type,
        amount_cents=amount_cents,
        currency=(group.currency or "BRL"),
        status="pending",
        due_date=payload.due_date,
        period_year=py,
        period_month=pm,
        description=payload.description,
        paid=False,
        paid_at=None,
        confirmed_by_user_id=None,
    )
    db.add(entry)
    db.flush()
    if notification_allowed(db, payload.user_id, "fines"):
        create_notification(
            db,
            user_id=payload.user_id,
            type="fine_applied",
            title="Multa aplicada",
            message=f"Foi aplicada uma multa no grupo {group.name}.",
            external_key=f"group_fine:{group.id}:{payload.user_id}:{entry.id}",
            payload={"group_id": group.id, "entry_id": entry.id, "match_id": payload.match_id},
        )
    db.commit()
    db.refresh(entry)
    return _entry_to_out(db, entry)


class GenerateMonthlyPayload(BaseModel):
    year: Optional[int] = None
    month: Optional[int] = None


@router.post("/{group_id}/finance/generate-monthly")
def generate_monthly_entries(
    group_id: str,
    payload: GenerateMonthlyPayload,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Gera mensalidades automaticamente (idempotente).

    - Apenas ADM/Owner
    - Cria 1 lançamento 'monthly' por membro com billing_type=monthly
    - Usa groups.payment_due_day para due_date
    - Usa groups.monthly_cost como valor
    """
    group, _ = require_group_admin_or_owner(db, group_id, current_user_id)

    if not getattr(group, "payment_due_day", None):
        raise HTTPException(status_code=400, detail="payment_due_day não configurado no grupo")

    amount = float(getattr(group, "monthly_cost", 0) or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="monthly_cost não configurado no grupo")

    today = date.today()
    year = int(payload.year or today.year)
    month = int(payload.month or today.month)
    if month < 1 or month > 12:
        raise HTTPException(status_code=400, detail="month inválido")

    # calcula due_date com clamp para último dia do mês
    last_day = calendar.monthrange(year, month)[1]
    due_day = int(getattr(group, "payment_due_day", 1) or 1)
    due_day = max(1, min(due_day, last_day))
    due = date(year, month, due_day)

    amount_cents = _cents(amount)

    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id)
        .filter(GroupMember.status == "active")
        .filter(GroupMember.billing_type == "monthly")
        .all()
    )

    created = 0
    skipped = 0
    for m in members:
        # idempotente pela unique constraint (group_id,user_id,entry_type,period)
        exists = (
            db.query(GroupFinancialEntry)
            .filter(GroupFinancialEntry.group_id == group_id)
            .filter(GroupFinancialEntry.user_id == m.user_id)
            .filter(GroupFinancialEntry.entry_type == "monthly")
            .filter(GroupFinancialEntry.period_year == year)
            .filter(GroupFinancialEntry.period_month == month)
            .first()
        )
        if exists:
            skipped += 1
            continue

        e = GroupFinancialEntry(
            group_id=group_id,
            user_id=m.user_id,
            match_id=None,
            entry_type="monthly",
            amount_cents=amount_cents,
            currency=(group.currency or "BRL"),
            status="pending",
            due_date=due,
            period_year=year,
            period_month=month,
            description=f"Mensalidade {month:02d}/{year}",
            paid=False,
            paid_at=None,
            confirmed_by_user_id=None,
        )
        db.add(e)
        created += 1

    db.commit()
    return {
        "ok": True,
        "group_id": group_id,
        "year": year,
        "month": month,
        "due_date": due.isoformat(),
        "created": created,
        "skipped": skipped,
    }


class FineCreatePayload(BaseModel):
    user_id: int
    match_id: Optional[int] = None
    due_date: Optional[date] = None
    reason: Optional[str] = None


@router.post("/{group_id}/finance/fines", response_model=FinancialEntryOut)
def create_fine(
    group_id: str,
    payload: FineCreatePayload,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Gera multa manual/automática (por ausência).

    - Apenas ADM/Owner
    - Usa groups.fine_enabled + groups.fine_amount
    - Idempotente por (group_id,user_id,entry_type,match_id) quando match_id informado
    """
    group, _ = require_group_admin_or_owner(db, group_id, current_user_id)

    if not getattr(group, "fine_enabled", False):
        raise HTTPException(status_code=400, detail="Multa não habilitada no grupo")
    amount = float(getattr(group, "fine_amount", 0) or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="fine_amount não configurado")

    # garante que alvo é membro ativo
    gm = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id)
        .filter(GroupMember.user_id == payload.user_id)
        .filter(GroupMember.status == "active")
        .first()
    )
    if not gm:
        raise HTTPException(status_code=404, detail="Usuário não é membro ativo do grupo")

    if payload.match_id is not None:
        exists = (
            db.query(GroupFinancialEntry)
            .filter(GroupFinancialEntry.group_id == group_id)
            .filter(GroupFinancialEntry.user_id == payload.user_id)
            .filter(GroupFinancialEntry.entry_type == "fine")
            .filter(GroupFinancialEntry.match_id == payload.match_id)
            .first()
        )
        if exists:
            return _entry_to_out(db, exists)

    entry = GroupFinancialEntry(
        group_id=group_id,
        user_id=payload.user_id,
        match_id=payload.match_id,
        entry_type="fine",
        amount_cents=_cents(amount),
        currency=(group.currency or "BRL"),
        status="pending",
        due_date=payload.due_date or date.today(),
        description=payload.reason or "Multa",
        paid=False,
        paid_at=None,
        confirmed_by_user_id=None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_to_out(db, entry)


class MarkPaidPayload(BaseModel):
    paid: bool = True


@router.put("/{group_id}/finance/entries/{entry_id}/paid", response_model=FinancialEntryOut)
def mark_entry_paid(
    group_id: str,
    entry_id: int,
    payload: MarkPaidPayload,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """ADM/Owner confirma/desfaz pagamento."""
    require_group_admin_or_owner(db, group_id, current_user_id)

    entry = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .filter(GroupFinancialEntry.id == entry_id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Lançamento não encontrado")

    if payload.paid:
        entry.paid = True
        entry.status = "paid"
        entry.paid_at = utc_now()
        entry.confirmed_by_user_id = current_user_id
    else:
        entry.paid = False
        entry.status = "pending"
        entry.paid_at = None
        entry.confirmed_by_user_id = None

    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _entry_to_out(db, entry)


class DebtorOut(BaseModel):
    user_id: int
    user_name: Optional[str] = None
    user_avatar_url: Optional[str] = None
    pending_amount: float
    overdue_amount: float


@router.get("/{group_id}/finance/debtors", response_model=List[DebtorOut])
def list_debtors(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Lista jogadores em dívida (pendências). Apenas ADM/Owner."""
    require_group_admin_or_owner(db, group_id, current_user_id)

    today = date.today()
    entries = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .filter(GroupFinancialEntry.user_id.isnot(None))
        .filter(GroupFinancialEntry.paid.is_(False))
        .all()
    )

    per_user: dict[int, dict[str, int]] = {}
    for e in entries:
        uid = int(e.user_id)
        per_user.setdefault(uid, {"pending": 0, "overdue": 0})
        per_user[uid]["pending"] += int(e.amount_cents or 0)
        if e.due_date is not None and e.due_date < today:
            per_user[uid]["overdue"] += int(e.amount_cents or 0)

    out: List[DebtorOut] = []
    for uid, sums in per_user.items():
        if sums["pending"] <= 0 and sums["overdue"] <= 0:
            continue
        u = db.query(User).filter(User.id == uid).first()
        out.append(
            DebtorOut(
                user_id=uid,
                user_name=_user_display_name(u),
                user_avatar_url=(u.avatar_url if u else None),
                pending_amount=_money(sums["pending"]),
                overdue_amount=_money(sums["overdue"]),
            )
        )

    # ordena por maior overdue
    out.sort(key=lambda x: x.overdue_amount, reverse=True)
    return out


# =====================================================
# GROUP MATCHES (compat: /groups/{group_id}/matches)
# =====================================================
from datetime import datetime as _dt, timedelta
from sqlalchemy.exc import IntegrityError as _IntegrityError

from app.models import Match as _Match, MatchParticipant as _MatchParticipant, ParticipantStatus as _ParticipantStatus
from app.models import MatchJoinRequest as _MatchJoinRequest, JoinStatus as _JoinStatus, Player as _Player, User as _User
from app.permissions import require_group_admin as _require_group_admin, get_group_member as _get_group_member, get_user_primary_player as _get_user_primary_player


class GroupMatchCreateIn(BaseModel):
    starts_at: datetime
    ends_at: Optional[datetime] = None
    player_limit: int = Field(..., ge=0)

    title: Optional[str] = None
    status: Optional[str] = "scheduled"
    is_public: bool = False

    single_waitlist_release_days: int = 0

    price_cents: Optional[int] = None
    currency: Optional[str] = None

    city: Optional[str] = None
    location_name: Optional[str] = None
    notes: Optional[str] = None

    payment_method: Optional[str] = None
    payment_key: Optional[str] = None


class GroupMatchUpdateIn(BaseModel):
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    player_limit: Optional[int] = Field(None, ge=0)

    title: Optional[str] = None
    status: Optional[str] = None
    is_public: Optional[bool] = None

    single_waitlist_release_days: Optional[int] = None

    price_cents: Optional[int] = None
    currency: Optional[str] = None

    city: Optional[str] = None
    location_name: Optional[str] = None
    notes: Optional[str] = None

    payment_method: Optional[str] = None
    payment_key: Optional[str] = None


@router.get("/{group_id}/matches", response_model=List[dict])
def list_group_matches(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Lista partidas do grupo.

    Importante: esta rota existe para manter compatibilidade com o Flutter atual.
    """
    try:
        # qualquer membro pode listar as partidas do grupo
        _get_group_member(db, group_id, current_user_id)

        matches = (
            db.query(_Match)
            .filter(_Match.group_id == group_id)
            .order_by(_Match.starts_at.desc())
            .all()
        )

        # Retorna como dict simples pra manter compat com Flutter (MatchModel.fromJson)
        return [
            {
                "id": m.id,
                "group_id": m.group_id,
                "owner_id": m.owner_id,
                "status": m.status,
                "starts_at": m.starts_at.isoformat() if getattr(m, "starts_at", None) else None,
                "ends_at": m.ends_at.isoformat() if getattr(m, "ends_at", None) else None,
                "player_limit": m.player_limit,
                "title": m.title,
                "price_cents": m.price_cents,
                "currency": m.currency,
                "city": m.city,
                "location_name": m.location_name,
                "notes": m.notes,
                "is_public": bool(m.is_public),
                "payment_method": m.payment_method,
                "payment_key": m.payment_key,
                "single_waitlist_release_days": m.single_waitlist_release_days,
            }
            for m in matches
        ]
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        raise HTTPException(status_code=500, detail=f"DB error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unhandled: {str(e)}")


def _require_group_match(db: Session, group_id: str, match_id: int) -> _Match:
    m = db.query(_Match).filter(_Match.id == match_id).first()
    if not m or m.group_id != group_id:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return m


@router.post("/{group_id}/matches", response_model=dict, status_code=201)
def create_group_match(
    group_id: str,
    payload: GroupMatchCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    # apenas admin/owner
    _require_group_admin(db, group_id, current_user_id)

    ends_at = payload.ends_at or (payload.starts_at + timedelta(hours=2))
    if ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")

    value_per_player = None
    if payload.price_cents is not None:
        try:
            value_per_player = float(int(payload.price_cents) / 100.0)
        except Exception:
            value_per_player = 0.0

    m = _Match(
        owner_id=current_user_id,
        group_id=group_id,
        starts_at=payload.starts_at,
        ends_at=ends_at,
        date_time=payload.starts_at,
        title=payload.title,
        status=(payload.status or "scheduled"),
        city=payload.city,
        location_name=payload.location_name,
        venue_name=payload.location_name or payload.title or payload.city or "Partida",
        notes=payload.notes,
        is_public=bool(payload.is_public),
        player_limit=payload.player_limit,
        single_waitlist_release_days=payload.single_waitlist_release_days or 0,
        price_cents=payload.price_cents,
        value_per_player=value_per_player if value_per_player is not None else 0.0,
        currency=(payload.currency or None),
        payment_method=payload.payment_method,
        payment_key=payload.payment_key,
    )
    try:
        db.add(m)
        db.commit()
        db.refresh(m)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar partida: {e}")

    # criador entra como participante confirmado
    player = _get_user_primary_player(db, current_user_id)
    try:
        p = _MatchParticipant(
            match_id=m.id,
            user_id=current_user_id,
            player_id=player.id,
            status=_ParticipantStatus.confirmed.value,
        )
        db.add(p)
        db.commit()
    except _IntegrityError:
        db.rollback()
    except Exception:
        # Não devolver 500 após a partida já ter sido criada por falha secundária
        db.rollback()

    db.refresh(m)

    return {
        "id": m.id,
        "group_id": m.group_id,
        "owner_id": m.owner_id,
        "status": m.status,
        "starts_at": m.starts_at.isoformat() if m.starts_at else None,
        "ends_at": m.ends_at.isoformat() if m.ends_at else None,
        "player_limit": m.player_limit,
        "title": m.title,
        "price_cents": m.price_cents,
        "currency": m.currency,
        "city": m.city,
        "location_name": m.location_name,
        "notes": m.notes,
        "is_public": bool(m.is_public),
        "payment_method": m.payment_method,
        "payment_key": m.payment_key,
        "single_waitlist_release_days": m.single_waitlist_release_days,
    }


@router.get("/{group_id}/matches/{match_id}", response_model=dict)
def get_group_match(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _get_group_member(db, group_id, current_user_id)
    m = _require_group_match(db, group_id, match_id)

    return {
        "id": m.id,
        "group_id": m.group_id,
        "owner_id": m.owner_id,
        "status": m.status,
        "starts_at": m.starts_at.isoformat() if m.starts_at else None,
        "ends_at": m.ends_at.isoformat() if m.ends_at else None,
        "player_limit": m.player_limit,
        "title": m.title,
        "price_cents": m.price_cents,
        "currency": m.currency,
        "city": m.city,
        "location_name": m.location_name,
        "notes": m.notes,
        "is_public": bool(m.is_public),
        "payment_method": m.payment_method,
        "payment_key": m.payment_key,
        "single_waitlist_release_days": m.single_waitlist_release_days,
    }


@router.put("/{group_id}/matches/{match_id}", response_model=dict)
def update_group_match(
    group_id: str,
    match_id: int,
    payload: GroupMatchUpdateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _require_group_admin(db, group_id, current_user_id)
    m = _require_group_match(db, group_id, match_id)

    data = payload.model_dump(exclude_unset=True)

    starts_at = data.get("starts_at", m.starts_at)
    ends_at = data.get("ends_at", m.ends_at)
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")

    for k, v in data.items():
        setattr(m, k, v)

    if "starts_at" in data and m.starts_at is not None:
        m.date_time = m.starts_at

    if "location_name" in data or "title" in data:
        m.venue_name = m.location_name or m.title or m.venue_name or "Partida"

    db.add(m)
    db.commit()
    db.refresh(m)

    return {
        "id": m.id,
        "group_id": m.group_id,
        "owner_id": m.owner_id,
        "status": m.status,
        "starts_at": m.starts_at.isoformat() if m.starts_at else None,
        "ends_at": m.ends_at.isoformat() if m.ends_at else None,
        "player_limit": m.player_limit,
        "title": m.title,
        "price_cents": m.price_cents,
        "currency": m.currency,
        "city": m.city,
        "location_name": m.location_name,
        "notes": m.notes,
        "is_public": bool(m.is_public),
        "payment_method": m.payment_method,
        "payment_key": m.payment_key,
        "single_waitlist_release_days": m.single_waitlist_release_days,
    }


@router.delete("/{group_id}/matches/{match_id}")
def delete_group_match(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _require_group_admin(db, group_id, current_user_id)
    _require_group_match(db, group_id, match_id)

    try:
        # Evita carregar relacionamentos ORM que podem quebrar em bases legadas
        # (ex.: model Payment espera owner_id, mas a coluna ainda não existe no schema).
        db.execute(text("DELETE FROM match_comments WHERE match_id = :match_id"), {"match_id": match_id})
        db.execute(text("DELETE FROM match_events WHERE match_id = :match_id"), {"match_id": match_id})
        db.execute(text("DELETE FROM match_draw_teams WHERE match_id = :match_id"), {"match_id": match_id})
        db.execute(text("DELETE FROM match_join_requests WHERE match_id = :match_id"), {"match_id": match_id})
        db.execute(text("DELETE FROM payments WHERE match_id = :match_id"), {"match_id": match_id})
        db.execute(text("DELETE FROM group_financial_entries WHERE match_id = :match_id"), {"match_id": match_id})
        db.execute(text("DELETE FROM match_participants WHERE match_id = :match_id"), {"match_id": match_id})
        db.execute(text("DELETE FROM match_guests WHERE match_id = :match_id"), {"match_id": match_id})
        result = db.execute(
            text("DELETE FROM matches WHERE id = :match_id AND group_id = :group_id"),
            {"match_id": match_id, "group_id": group_id},
        )
        if (result.rowcount or 0) == 0:
            db.rollback()
            raise HTTPException(status_code=404, detail="Partida não encontrada")
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir partida: {e}")

    return {"ok": True}


# -------------------------------------------------
# MATCH JOIN REQUESTS (jogadores fora do grupo)
# Rotas na hierarquia do grupo: /groups/{group_id}/matches/{match_id}/...
# -------------------------------------------------

class MatchJoinRequestCreateIn(BaseModel):
    message: Optional[str] = None


class MatchJoinRequesterOut(BaseModel):
    user_id: int
    player_id: int
    name: str
    position: Optional[str] = None
    rating: Optional[int] = None


class MatchJoinRequestOut(BaseModel):
    id: int
    match_id: int
    group_id: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
    message: Optional[str] = None
    requester: MatchJoinRequesterOut


def _to_match_join_request_out(db: Session, req: _MatchJoinRequest) -> MatchJoinRequestOut:
    # We always have player_id (MatchJoinRequest requires it)
    player = db.query(_Player).filter(_Player.id == req.player_id).first()
    user_id = req.user_id
    name = getattr(player, "name", None) or "Jogador"
    position = getattr(player, "position", None)
    rating = getattr(player, "rating", None)

    return MatchJoinRequestOut(
        id=req.id,
        match_id=req.match_id,
        group_id=req.group_id,
        status=req.status,
        created_at=req.created_at,
        updated_at=req.updated_at,
        message=getattr(req, "message", None),
        requester=MatchJoinRequesterOut(
            user_id=user_id,
            player_id=req.player_id,
            name=name,
            position=position,
            rating=rating,
        ),
    )


@router.post("/{group_id}/matches/{match_id}/join-requests", response_model=MatchJoinRequestOut, status_code=201)
def create_match_join_request(
    group_id: str,
    match_id: int,
    payload: MatchJoinRequestCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Jogador fora do grupo solicita vaga em partida pública."""

    m = _require_group_match(db, group_id, match_id)

    if not bool(m.is_public):
        raise HTTPException(status_code=403, detail="Esta partida não está aberta para solicitações")
    if (m.status or "").lower() != "scheduled":
        raise HTTPException(status_code=400, detail="Partida não está disponível para solicitações")

    # Se já é membro ativo, ele deve usar o fluxo normal de presença (attendance)
    try:
        _get_group_member(db, group_id, current_user_id)
        raise HTTPException(status_code=400, detail="Você já é membro do grupo. Use a confirmação de presença.")
    except HTTPException as e:
        # 403 => não é membro; ok
        if e.status_code not in (403,):
            raise

    player = _get_user_primary_player(db, current_user_id)

    existing = (
        db.query(_MatchJoinRequest)
        .filter(_MatchJoinRequest.match_id == match_id)
        .filter(_MatchJoinRequest.player_id == player.id)
        .first()
    )
    if existing:
        # Reabre se foi rejeitado anteriormente
        if (existing.status or "").lower() == _JoinStatus.rejected.value:
            existing.status = _JoinStatus.pending.value
        if hasattr(existing, "message"):
            existing.message = payload.message
        if hasattr(existing, "group_id"):
            existing.group_id = group_id
        db.add(existing)
        db.commit()
        db.refresh(existing)
        log_event(logger, "match_join_request_reopened", user_id=current_user_id, group_id=group_id, match_id=match_id, request_id=existing.id)
        return _to_match_join_request_out(db, existing)

    req = _MatchJoinRequest(
        match_id=match_id,
        user_id=current_user_id,
        player_id=player.id,
        status=_JoinStatus.pending.value,
        group_id=group_id,
        message=payload.message,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    log_event(logger, "match_join_request_created", user_id=current_user_id, group_id=group_id, match_id=match_id, request_id=req.id)
    return _to_match_join_request_out(db, req)


@router.get("/{group_id}/matches/{match_id}/join-requests", response_model=List[MatchJoinRequestOut])
def list_match_join_requests(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Owner/ADM vê solicitações de não-membros para a partida."""
    _require_group_admin(db, group_id, current_user_id)
    _require_group_match(db, group_id, match_id)

    reqs = (
        db.query(_MatchJoinRequest)
        .filter(_MatchJoinRequest.match_id == match_id)
        .filter((_MatchJoinRequest.status == _JoinStatus.pending.value) | (_MatchJoinRequest.status == "pending"))
        .order_by(_MatchJoinRequest.created_at.asc())
        .all()
    )
    return [_to_match_join_request_out(db, r) for r in reqs]


@router.post("/{group_id}/matches/{match_id}/join-requests/{request_id}/approve")
def approve_match_join_request(
    group_id: str,
    match_id: int,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Owner/ADM aprova solicitação e adiciona como participante (confirmed/waitlist)."""
    _require_group_admin(db, group_id, current_user_id)
    m = _require_group_match(db, group_id, match_id)

    req = (
        db.query(_MatchJoinRequest)
        .filter(_MatchJoinRequest.match_id == match_id)
        .filter(_MatchJoinRequest.id == request_id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    if (req.status or "").lower() != _JoinStatus.pending.value:
        return {"ok": True}

    confirmed_count = (
        db.query(_MatchParticipant)
        .filter(_MatchParticipant.match_id == match_id)
        .filter(_MatchParticipant.status == _ParticipantStatus.confirmed.value)
        .count()
    )
    capacity_ok = (m.player_limit or 0) <= 0 or confirmed_count < (m.player_limit or 0)
    participant_status = _ParticipantStatus.confirmed.value if capacity_ok else _ParticipantStatus.waitlist.value

    exists = (
        db.query(_MatchParticipant)
        .filter(_MatchParticipant.match_id == match_id)
        .filter(_MatchParticipant.player_id == req.player_id)
        .first()
    )
    if not exists:
        db.add(_MatchParticipant(match_id=match_id, user_id=req.user_id, player_id=req.player_id, status=participant_status))

    req.status = _JoinStatus.active.value
    if hasattr(req, "reviewed_by_user_id"):
        req.reviewed_by_user_id = current_user_id
    if hasattr(req, "reviewed_at"):
        req.reviewed_at = utc_now()

    db.add(req)
    db.commit()
    audit_admin_action(logger, action="approve_match_join_request", actor_user_id=current_user_id, group_id=group_id, match_id=match_id, target_request_id=request_id, target_user_id=req.user_id, target_player_id=req.player_id, participant_status=participant_status)
    return {"ok": True}


@router.post("/{group_id}/matches/{match_id}/join-requests/{request_id}/reject")
def reject_match_join_request(
    group_id: str,
    match_id: int,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    _require_group_admin(db, group_id, current_user_id)
    _require_group_match(db, group_id, match_id)

    req = (
        db.query(_MatchJoinRequest)
        .filter(_MatchJoinRequest.match_id == match_id)
        .filter(_MatchJoinRequest.id == request_id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")

    req.status = _JoinStatus.rejected.value
    if hasattr(req, "reviewed_by_user_id"):
        req.reviewed_by_user_id = current_user_id
    if hasattr(req, "reviewed_at"):
        req.reviewed_at = utc_now()
    db.add(req)
    db.commit()
    audit_admin_action(logger, action="reject_match_join_request", actor_user_id=current_user_id, group_id=group_id, match_id=match_id, target_request_id=request_id, target_user_id=req.user_id, target_player_id=req.player_id)
    return {"ok": True}
