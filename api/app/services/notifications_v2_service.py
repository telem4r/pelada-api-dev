from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.core.cache import app_cache
from app.repositories.notifications_v2 import NotificationsV2Repository
from app.schemas.notifications_v2 import NotificationRealtimeSnapshotV2Model, NotificationUnreadCountV2Model, NotificationV2Model


class NotificationsV2Service:
    def _cache_key(self, kind: str, user_id: str, group_id: str | None = None) -> str:
        key = f"notifications_v2:{kind}:user:{user_id}"
        if group_id:
            key += f":group:{group_id}"
        return key

    def _invalidate(self, *, user_id: str | None = None, group_id: str | None = None) -> None:
        if user_id:
            app_cache.invalidate_prefix(f"notifications_v2:list:user:{user_id}")
            app_cache.invalidate_prefix(f"notifications_v2:count:user:{user_id}")
            app_cache.invalidate_prefix(f"notifications_v2:realtime:user:{user_id}")
        elif group_id:
            app_cache.invalidate_prefix('notifications_v2:')

    def __init__(self, repository: NotificationsV2Repository | None = None) -> None:
        self.repository = repository or NotificationsV2Repository()

    def _list(self, db: Session, principal: SupabasePrincipal, *, unread_only: bool, limit: int, group_id: str | None = None) -> list[NotificationV2Model]:
        rows = self.repository.list_for_user(db, user_id=principal.user_id, unread_only=unread_only, limit=limit, group_id=group_id)
        return [NotificationV2Model(**row) for row in rows]

    def list_notifications(self, db: Session, principal: SupabasePrincipal, *, unread_only: bool = False, limit: int = 50, group_id: str | None = None) -> list[NotificationV2Model]:
        key = self._cache_key('list', principal.user_id, group_id)
        return app_cache.get_or_set(key + (':unread' if unread_only else ':all') + f':limit:{limit}', lambda: self._list(db, principal, unread_only=unread_only, limit=limit, group_id=group_id), 15)

    def unread_count(self, db: Session, principal: SupabasePrincipal, *, group_id: str | None = None) -> NotificationUnreadCountV2Model:
        key = self._cache_key('count', principal.user_id, group_id)
        return app_cache.get_or_set(key, lambda: NotificationUnreadCountV2Model(unread_count=self.repository.unread_count(db, user_id=principal.user_id, group_id=group_id)), 10)

    def mark_read(self, db: Session, principal: SupabasePrincipal, notification_id: str) -> NotificationUnreadCountV2Model:
        ok = self.repository.mark_one_read(db, notification_id=notification_id, user_id=principal.user_id)
        db.commit()
        self._invalidate(user_id=principal.user_id)
        if not ok:
            raise HTTPException(status_code=404, detail='Notificação não encontrada.')
        return self.unread_count(db, principal)

    def mark_all_read(self, db: Session, principal: SupabasePrincipal, *, group_id: str | None = None) -> NotificationUnreadCountV2Model:
        self.repository.mark_all_read(db, user_id=principal.user_id, group_id=group_id)
        db.commit()
        self._invalidate(user_id=principal.user_id, group_id=group_id)
        return self.unread_count(db, principal, group_id=group_id)

    def realtime_snapshot(self, db: Session, principal: SupabasePrincipal, *, group_id: str | None = None, limit: int = 10) -> NotificationRealtimeSnapshotV2Model:
        key = self._cache_key('realtime', principal.user_id, group_id) + f':limit:{limit}'
        def _load():
            items = self._list(db, principal, unread_only=False, limit=limit, group_id=group_id)
            latest = self.repository.latest_created_at(db, user_id=principal.user_id, group_id=group_id)
            unread = self.repository.unread_count(db, user_id=principal.user_id, group_id=group_id)
            return NotificationRealtimeSnapshotV2Model(unread_count=unread, latest_created_at=latest, items=items)
        return app_cache.get_or_set(key, _load, 10)

    def notify_group(self, db: Session, *, group_id: str, actor_user_id: str | None, event_type: str, title: str, message: str, payload: dict | None = None, exclude_user_id: str | None = None) -> int:
        recipients = self.repository.list_group_member_user_ids(db, group_id=group_id, exclude_user_id=exclude_user_id)
        if not recipients:
            return 0
        inserted = self.repository.insert_many(db, recipient_user_ids=recipients, group_id=group_id, actor_user_id=actor_user_id, event_type=event_type, title=title, message=message, payload=payload)
        self._invalidate(group_id=group_id)
        return inserted
