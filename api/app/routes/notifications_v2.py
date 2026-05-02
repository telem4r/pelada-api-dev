from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.notifications_v2 import NotificationRealtimeSnapshotV2Model, NotificationUnreadCountV2Model, NotificationV2Model
from app.services.notifications_v2_service import NotificationsV2Service

router = APIRouter(tags=['Notifications V2'])
service = NotificationsV2Service()


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get('/v2/notifications', response_model=list[NotificationV2Model])
def list_notifications(unread_only: bool = False, limit: int = Query(default=50, ge=1, le=100), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_notifications(db, principal, unread_only=unread_only, limit=limit)


@router.get('/v2/notifications/unread-count', response_model=NotificationUnreadCountV2Model)
def unread_count(principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.unread_count(db, principal)


@router.post('/v2/notifications/read-all', response_model=NotificationUnreadCountV2Model)
def mark_all_read(principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_all_read(db, principal)


@router.post('/v2/notifications/{notification_id}/read', response_model=NotificationUnreadCountV2Model)
def mark_read(notification_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_read(db, principal, notification_id)


@router.get('/v2/notifications/realtime', response_model=NotificationRealtimeSnapshotV2Model)
def realtime_snapshot(limit: int = Query(default=10, ge=1, le=50), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.realtime_snapshot(db, principal, limit=limit)


@router.get('/v2/groups/{group_id}/notifications', response_model=list[NotificationV2Model])
def list_group_notifications(group_id: str, unread_only: bool = False, limit: int = Query(default=50, ge=1, le=100), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_notifications(db, principal, unread_only=unread_only, limit=limit, group_id=group_id)


@router.get('/v2/groups/{group_id}/notifications/realtime', response_model=NotificationRealtimeSnapshotV2Model)
def realtime_group_snapshot(group_id: str, limit: int = Query(default=10, ge=1, le=50), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.realtime_snapshot(db, principal, group_id=group_id, limit=limit)


@router.post('/v2/groups/{group_id}/notifications/read-all', response_model=NotificationUnreadCountV2Model)
def mark_group_read_all(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_all_read(db, principal, group_id=group_id)
