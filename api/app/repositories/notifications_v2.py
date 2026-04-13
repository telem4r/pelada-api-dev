from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import json

from sqlalchemy import text
from sqlalchemy.orm import Session


class NotificationsV2Repository:
    def list_for_user(self, db: Session, *, user_id: str, unread_only: bool = False, limit: int = 50, group_id: str | None = None) -> list[dict[str, Any]]:
        sql = text(
            """
            select
                id::text as notification_id,
                recipient_user_id::text as recipient_user_id,
                group_id::text as group_id,
                event_type,
                title,
                message,
                coalesce(payload, '{}'::jsonb) as payload,
                is_read,
                created_at
            from public.notification_events_v2
            where recipient_user_id = cast(:user_id as uuid)
              and (:group_id is null or group_id = cast(:group_id as uuid))
              and (:unread_only = false or is_read = false)
            order by created_at desc
            limit :limit
            """
        )
        rows = db.execute(sql, {'user_id': user_id, 'group_id': group_id, 'unread_only': unread_only, 'limit': limit}).mappings().all()
        return [dict(row) for row in rows]

    def unread_count(self, db: Session, *, user_id: str, group_id: str | None = None) -> int:
        sql = text(
            """
            select count(*)
            from public.notification_events_v2
            where recipient_user_id = cast(:user_id as uuid)
              and (:group_id is null or group_id = cast(:group_id as uuid))
              and is_read = false
            """
        )
        return int(db.execute(sql, {'user_id': user_id, 'group_id': group_id}).scalar() or 0)

    def latest_created_at(self, db: Session, *, user_id: str, group_id: str | None = None):
        sql = text(
            """
            select max(created_at)
            from public.notification_events_v2
            where recipient_user_id = cast(:user_id as uuid)
              and (:group_id is null or group_id = cast(:group_id as uuid))
            """
        )
        return db.execute(sql, {'user_id': user_id, 'group_id': group_id}).scalar()

    def mark_one_read(self, db: Session, *, notification_id: str, user_id: str) -> bool:
        result = db.execute(
            text(
                """
                update public.notification_events_v2
                   set is_read = true,
                       read_at = now()
                 where id = cast(:notification_id as uuid)
                   and recipient_user_id = cast(:user_id as uuid)
                """
            ),
            {'notification_id': notification_id, 'user_id': user_id},
        )
        return bool(result.rowcount)

    def mark_all_read(self, db: Session, *, user_id: str, group_id: str | None = None) -> int:
        result = db.execute(
            text(
                """
                update public.notification_events_v2
                   set is_read = true,
                       read_at = now()
                 where recipient_user_id = cast(:user_id as uuid)
                   and (:group_id is null or group_id = cast(:group_id as uuid))
                   and is_read = false
                """
            ),
            {'user_id': user_id, 'group_id': group_id},
        )
        return int(result.rowcount or 0)

    def list_group_member_user_ids(self, db: Session, *, group_id: str, exclude_user_id: str | None = None) -> list[str]:
        rows = db.execute(
            text(
                """
                select distinct user_id::text as user_id
                from public.group_members
                where group_id = cast(:group_id as uuid)
                  and status = 'active'
                  and (:exclude_user_id is null or user_id <> cast(:exclude_user_id as uuid))
                """
            ),
            {'group_id': group_id, 'exclude_user_id': exclude_user_id},
        ).mappings().all()
        return [str(row['user_id']) for row in rows]

    def insert_many(self, db: Session, *, recipient_user_ids: Iterable[str], group_id: str | None, actor_user_id: str | None, event_type: str, title: str, message: str, payload: dict[str, Any] | None = None) -> int:
        payload = payload or {}
        sql = text(
            """
            insert into public.notification_events_v2 (
                recipient_user_id,
                group_id,
                actor_user_id,
                event_type,
                title,
                message,
                payload,
                is_read
            ) values (
                cast(:recipient_user_id as uuid),
                cast(:group_id as uuid),
                cast(:actor_user_id as uuid),
                :event_type,
                :title,
                :message,
                cast(:payload as jsonb),
                false
            )
            """
        )
        total = 0
        for recipient_user_id in recipient_user_ids:
            db.execute(sql, {
                'recipient_user_id': recipient_user_id,
                'group_id': group_id,
                'actor_user_id': actor_user_id,
                'event_type': event_type,
                'title': title,
                'message': message,
                'payload': json.dumps(payload),
            })
            total += 1
        return total
