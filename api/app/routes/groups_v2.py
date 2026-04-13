
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.groups import (
    GroupCreateV2Request,
    GroupInvitationCreateV2Request,
    GroupInvitationV2Model,
    GroupJoinRequestV2Model,
    GroupMemberBillingUpdateV2Request,
    GroupMemberRoleUpdateV2Request,
    GroupMemberSummaryV2Model,
    GroupSummaryV2Model,
    GroupUpdateV2Request,
)
from app.services.groups_v2_service import GroupsV2Service

router = APIRouter(prefix='/v2/groups', tags=['Groups V2'])
service = GroupsV2Service()

def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

@router.get('/mine', response_model=list[GroupSummaryV2Model])
def list_my_groups(principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_my_groups(db, principal)

@router.get('/search', response_model=list[GroupSummaryV2Model])
def search_groups(q: str = Query('', max_length=120), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.search_groups(db, principal, q)

@router.post('', response_model=GroupSummaryV2Model)
def create_group(payload: GroupCreateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.create_group(db, principal, payload)

@router.get('/{group_id}', response_model=GroupSummaryV2Model)
def get_group(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_group(db, principal, group_id)

@router.put('/{group_id}', response_model=GroupSummaryV2Model)
def update_group(group_id: str, payload: GroupUpdateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_group(db, principal, group_id, payload)

@router.get('/{group_id}/members', response_model=list[GroupMemberSummaryV2Model])
def list_members(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_members(db, principal, group_id)

@router.get('/{group_id}/members/me')
def my_membership(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_my_membership(db, principal, group_id)

@router.delete('/{group_id}/leave')
def leave_group(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.leave_group(db, principal, group_id)

@router.delete('/{group_id}/members/{member_user_id}')
def remove_member(group_id: str, member_user_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.remove_member(db, principal, group_id, member_user_id)

@router.put('/{group_id}/members/{member_user_id}/role')
def update_member_role(group_id: str, member_user_id: str, payload: GroupMemberRoleUpdateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_member_role(db, principal, group_id, member_user_id, payload)

@router.put('/{group_id}/members/{member_user_id}/billing')
def update_member_billing(group_id: str, member_user_id: str, payload: GroupMemberBillingUpdateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_member_billing(db, principal, group_id, member_user_id, payload)

@router.post('/{group_id}/join-requests', response_model=GroupJoinRequestV2Model)
def request_join(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.request_join(db, principal, group_id)

@router.get('/{group_id}/join-requests', response_model=list[GroupJoinRequestV2Model])
def list_pending_join_requests(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_pending_join_requests(db, principal, group_id)

@router.post('/{group_id}/join-requests/{request_id}/approve', response_model=GroupJoinRequestV2Model)
def approve_join_request(group_id: str, request_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.approve_join_request(db, principal, group_id, request_id)

@router.post('/{group_id}/join-requests/{request_id}/reject', response_model=GroupJoinRequestV2Model)
def reject_join_request(group_id: str, request_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.reject_join_request(db, principal, group_id, request_id)

@router.post('/{group_id}/invite', response_model=GroupInvitationV2Model)
def create_invitation(group_id: str, payload: GroupInvitationCreateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.create_invitation(db, principal, group_id, payload)


# ── PATCH aliases (frontend envia PATCH, backend original usava PUT) ──

@router.patch('/{group_id}/members/{member_user_id}/role')
def patch_member_role(group_id: str, member_user_id: str, payload: GroupMemberRoleUpdateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_member_role(db, principal, group_id, member_user_id, payload)


@router.patch('/{group_id}/members/{member_user_id}/billing')
def patch_member_billing(group_id: str, member_user_id: str, payload: GroupMemberBillingUpdateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_member_billing(db, principal, group_id, member_user_id, payload)


# ── Skill Rating (Bug 6) ─────────────────────────────────────────────

@router.patch('/{group_id}/members/{member_user_id}/skill-rating')
def patch_member_skill_rating(group_id: str, member_user_id: str, payload: dict, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_member_skill_rating(db, principal, group_id, member_user_id, payload)
