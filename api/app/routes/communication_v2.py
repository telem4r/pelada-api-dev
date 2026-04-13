"""Communication V2 routes — announcements, comments, activity, invites, notification-settings.

All endpoints use Supabase JWT auth and UUID-native tables.
These routes cover frontend antigo features that were not part of the original V2 migration.
"""
from __future__ import annotations

from typing import Any, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal

router = APIRouter(tags=["Communication V2"])


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── Helpers ──────────────────────────────────────────────────────────────

def _table_columns(db: Session, table_name: str) -> set[str]:
    rows = db.execute(text("""
        select column_name
        from information_schema.columns
        where table_schema = 'public' and table_name = :table_name
    """), {'table_name': table_name}).scalars().all()
    return {str(row) for row in rows}


def _group_invitations_responded_at_expr(db: Session) -> str:
    cols = _table_columns(db, 'group_invitations')
    return 'gi.responded_at' if 'responded_at' in cols else 'null::timestamptz'


def _resolve_identity(db: Session, user_id: str) -> dict[str, Any]:
    row = db.execute(text("""
        select u.id::text as user_id, p.id::text as player_id,
               coalesce(nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(u.name), ''), 'Jogador') as name,
               coalesce(p.avatar_url, u.avatar_url) as avatar_url
        from public.users u
        join public.players p on p.user_id = u.id
        where u.id = cast(:uid as uuid) limit 1
    """), {'uid': user_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado.")
    return dict(row)


def _require_group_member(db: Session, group_id: str, user_id: str) -> dict[str, Any]:
    row = db.execute(text("""
        select gm.role::text as role, gm.status::text as status, gm.player_id::text as player_id
        from public.group_members gm
        join public.players p on p.id = gm.player_id
        where gm.group_id = cast(:gid as uuid) and p.user_id = cast(:uid as uuid)
        limit 1
    """), {'gid': group_id, 'uid': user_id}).mappings().first()
    if not row or row['status'] != 'active':
        raise HTTPException(status_code=403, detail="Não é membro ativo deste grupo.")
    return dict(row)


def _require_admin(db: Session, group_id: str, user_id: str) -> dict[str, Any]:
    m = _require_group_member(db, group_id, user_id)
    if m['role'] not in ('owner', 'admin'):
        raise HTTPException(status_code=403, detail="Apenas admin/owner pode realizar esta ação.")
    return m


# ═══════════════════════════════════════════════════════════════════════
# ANNOUNCEMENTS
# ═══════════════════════════════════════════════════════════════════════

class AnnouncementCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1)
    is_pinned: bool = False

class AnnouncementOut(BaseModel):
    id: str
    group_id: str
    title: str
    message: str
    is_pinned: bool
    published_at: Optional[datetime] = None
    author: dict = {}


@router.post("/v2/groups/{group_id}/announcements", response_model=AnnouncementOut, status_code=201)
def create_announcement(group_id: str, payload: AnnouncementCreate,
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    identity = _resolve_identity(db, principal.user_id)
    row = db.execute(text("""
        insert into public.group_announcements_v2 (group_id, author_user_id, title, message, is_pinned)
        values (cast(:gid as uuid), cast(:uid as uuid), :title, :message, :pinned)
        returning id::text, group_id::text, title, message, is_pinned, published_at
    """), {'gid': group_id, 'uid': principal.user_id, 'title': payload.title,
           'message': payload.message, 'pinned': payload.is_pinned}).mappings().first()
    db.commit()
    return AnnouncementOut(**dict(row), author={'name': identity['name']})


@router.get("/v2/groups/{group_id}/announcements", response_model=list[AnnouncementOut])
def list_announcements(group_id: str,
                       principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                       db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    rows = db.execute(text("""
        select a.id::text, a.group_id::text, a.title, a.message, a.is_pinned, a.published_at,
               coalesce(nullif(trim(u.name), ''), 'Jogador') as author_name
        from public.group_announcements_v2 a
        join public.users u on u.id = a.author_user_id
        where a.group_id = cast(:gid as uuid)
        order by a.is_pinned desc, a.published_at desc
    """), {'gid': group_id}).mappings().all()
    return [AnnouncementOut(id=r['id'], group_id=r['group_id'], title=r['title'], message=r['message'],
                            is_pinned=r['is_pinned'], published_at=r['published_at'],
                            author={'name': r['author_name']}) for r in rows]


@router.put("/v2/groups/{group_id}/announcements/{announcement_id}", response_model=AnnouncementOut)
def update_announcement(group_id: str, announcement_id: str, payload: AnnouncementCreate,
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    row = db.execute(text("""
        update public.group_announcements_v2
        set title = :title, message = :message, is_pinned = :pinned, updated_at = now()
        where id = cast(:aid as uuid) and group_id = cast(:gid as uuid)
        returning id::text, group_id::text, title, message, is_pinned, published_at
    """), {'gid': group_id, 'aid': announcement_id, 'title': payload.title,
           'message': payload.message, 'pinned': payload.is_pinned}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado.")
    db.commit()
    identity = _resolve_identity(db, principal.user_id)
    return AnnouncementOut(**dict(row), author={'name': identity['name']})


@router.delete("/v2/groups/{group_id}/announcements/{announcement_id}", status_code=204)
def delete_announcement(group_id: str, announcement_id: str,
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    result = db.execute(text("""
        delete from public.group_announcements_v2
        where id = cast(:aid as uuid) and group_id = cast(:gid as uuid)
    """), {'gid': group_id, 'aid': announcement_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Anúncio não encontrado.")
    db.commit()


# ═══════════════════════════════════════════════════════════════════════
# MATCH COMMENTS
# ═══════════════════════════════════════════════════════════════════════

class CommentCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)

class CommentOut(BaseModel):
    id: str
    match_id: str
    group_id: str
    message: str
    created_at: Optional[datetime] = None
    author: dict = {}
    can_delete: bool = False


@router.get("/v2/groups/{group_id}/matches/{match_id}/comments", response_model=list[CommentOut])
def list_comments(group_id: str, match_id: str,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    rows = db.execute(text("""
        select c.id::text, c.match_id::text, c.group_id::text, c.message, c.created_at,
               c.author_user_id::text as author_uid,
               coalesce(nullif(trim(u.name), ''), 'Jogador') as author_name
        from public.match_comments_v2 c
        join public.users u on u.id = c.author_user_id
        where c.group_id = cast(:gid as uuid) and c.match_id = cast(:mid as uuid)
        order by c.created_at asc
    """), {'gid': group_id, 'mid': match_id}).mappings().all()
    return [CommentOut(id=r['id'], match_id=r['match_id'], group_id=r['group_id'],
                       message=r['message'], created_at=r['created_at'],
                       author={'name': r['author_name']},
                       can_delete=(r['author_uid'] == principal.user_id)) for r in rows]


@router.post("/v2/groups/{group_id}/matches/{match_id}/comments", response_model=CommentOut, status_code=201)
def create_comment(group_id: str, match_id: str, payload: CommentCreate,
                   principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                   db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    identity = _resolve_identity(db, principal.user_id)
    row = db.execute(text("""
        insert into public.match_comments_v2 (group_id, match_id, author_user_id, message)
        values (cast(:gid as uuid), cast(:mid as uuid), cast(:uid as uuid), :msg)
        returning id::text, match_id::text, group_id::text, message, created_at
    """), {'gid': group_id, 'mid': match_id, 'uid': principal.user_id, 'msg': payload.message}).mappings().first()
    db.commit()
    return CommentOut(**dict(row), author={'name': identity['name']}, can_delete=True)


@router.delete("/v2/groups/{group_id}/matches/{match_id}/comments/{comment_id}", status_code=204)
def delete_comment(group_id: str, match_id: str, comment_id: str,
                   principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                   db: Session = Depends(get_db_session)):
    membership = _require_group_member(db, group_id, principal.user_id)
    # Pode apagar se é autor ou admin
    result = db.execute(text("""
        delete from public.match_comments_v2
        where id = cast(:cid as uuid) and group_id = cast(:gid as uuid) and match_id = cast(:mid as uuid)
          and (author_user_id = cast(:uid as uuid) or :is_admin = true)
    """), {'cid': comment_id, 'gid': group_id, 'mid': match_id, 'uid': principal.user_id,
           'is_admin': membership['role'] in ('owner', 'admin')})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Comentário não encontrado ou sem permissão.")
    db.commit()


# ═══════════════════════════════════════════════════════════════════════
# ACTIVITY LOG
# ═══════════════════════════════════════════════════════════════════════

class ActivityOut(BaseModel):
    id: str
    activity_type: str
    title: str
    description: str
    created_at: Optional[datetime] = None


@router.get("/v2/groups/{group_id}/activity", response_model=list[ActivityOut])
def list_activity(group_id: str,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    rows = db.execute(text("""
        select id::text, activity_type, title, description, created_at
        from public.group_activity_v2
        where group_id = cast(:gid as uuid)
        order by created_at desc limit 50
    """), {'gid': group_id}).mappings().all()
    return [ActivityOut(**dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# INVITES (group_invitations table already exists in V2)
# ═══════════════════════════════════════════════════════════════════════

class InviteOut(BaseModel):
    id: str
    group_id: str
    invited_user_id: Optional[str] = None
    invited_user_name: str = "Usuário"
    invited_by_user_id: Optional[str] = None
    invited_by_name: Optional[str] = None
    group_name: Optional[str] = None
    email: Optional[str] = None
    status: str = "pending"
    created_at: Optional[datetime] = None
    responded_at: Optional[datetime] = None


@router.get("/v2/groups/{group_id}/invites", response_model=list[InviteOut])
def list_invites(group_id: str,
                 principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                 db: Session = Depends(get_db_session)):
    _require_group_member(db, group_id, principal.user_id)
    responded_at_expr = _group_invitations_responded_at_expr(db)
    rows = db.execute(text(f"""
        select gi.id::text, gi.group_id::text, gi.invited_email as email,
               gi.invited_by_user_id::text, gi.status::text,
               gi.created_at, {responded_at_expr} as responded_at,
               g.name as group_name,
               coalesce(nullif(trim(inv.name), ''), 'Jogador') as invited_by_name,
               coalesce(u2.id::text, null) as invited_user_id,
               coalesce(nullif(trim(u2.name), ''), gi.invited_email, 'Usuário') as invited_user_name
        from public.group_invitations gi
        join public.groups g on g.id = gi.group_id
        join public.users inv on inv.id = gi.invited_by_user_id
        left join public.users u2 on lower(u2.email) = lower(gi.invited_email)
        where gi.group_id = cast(:gid as uuid)
        order by gi.created_at desc
    """), {'gid': group_id}).mappings().all()
    return [InviteOut(**{**dict(r), 'status': 'pending' if dict(r).get('status') == 'invited' else dict(r).get('status')}) for r in rows]


@router.get("/v2/groups/invites/{invite_id}", response_model=InviteOut)
def get_invite(invite_id: str,
               principal: SupabasePrincipal = Depends(get_current_supabase_principal),
               db: Session = Depends(get_db_session)):
    responded_at_expr = _group_invitations_responded_at_expr(db)
    row = db.execute(text(f"""
        select gi.id::text, gi.group_id::text, gi.invited_email as email,
               gi.invited_by_user_id::text, gi.status::text,
               gi.created_at, {responded_at_expr} as responded_at,
               g.name as group_name,
               coalesce(nullif(trim(inv.name), ''), 'Jogador') as invited_by_name,
               null as invited_user_id,
               gi.invited_email as invited_user_name
        from public.group_invitations gi
        join public.groups g on g.id = gi.group_id
        join public.users inv on inv.id = gi.invited_by_user_id
        where gi.id = cast(:iid as uuid)
        limit 1
    """), {'iid': invite_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    return InviteOut(**{**dict(row), 'status': 'pending' if dict(row).get('status') == 'invited' else dict(row).get('status')})


@router.post("/v2/groups/invites/{invite_id}/accept")
def accept_invite(invite_id: str,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    identity = _resolve_identity(db, principal.user_id)
    invite = db.execute(text("""
        select gi.id::text as id,
               gi.group_id::text as group_id,
               lower(gi.invited_email) as invited_email,
               gi.status::text as status,
               g.group_type::text as group_type
        from public.group_invitations gi
        join public.groups g on g.id = gi.group_id
        where gi.id = cast(:iid as uuid)
        limit 1
    """), {'iid': invite_id}).mappings().first()
    if not invite:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")

    current_email = db.execute(text("""
        select lower(email) as email
        from public.users
        where id = cast(:uid as uuid)
        limit 1
    """), {'uid': principal.user_id}).scalar()

    if not current_email or current_email != invite['invited_email']:
        raise HTTPException(status_code=403, detail="Este convite não pertence ao utilizador autenticado.")

    if invite['status'] in ('accepted', 'rejected', 'removed'):
        raise HTTPException(status_code=400, detail="Convite já processado.")

    billing_type = 'avulso' if invite.get('group_type') == 'avulso' else 'mensalista'

    invitation_cols = _table_columns(db, 'group_invitations')
    if 'responded_at' in invitation_cols:
        db.execute(text("""
            update public.group_invitations
            set status = cast('accepted' as membership_status_enum),
                responded_at = now()
            where id = cast(:iid as uuid)
        """), {'iid': invite_id})
    else:
        db.execute(text("""
            update public.group_invitations
            set status = cast('accepted' as membership_status_enum)
            where id = cast(:iid as uuid)
        """), {'iid': invite_id})

    db.execute(text("""
        insert into public.group_members (
            id, group_id, user_id, player_id, role, status, billing_type, joined_at, created_at, updated_at
        ) values (
            gen_random_uuid(), cast(:gid as uuid), cast(:uid as uuid), cast(:pid as uuid),
            cast('member' as group_role_enum), cast('active' as membership_status_enum),
            cast(:billing_type as billing_type_enum), now(), now(), now()
        )
        on conflict (group_id, user_id) do update set
            player_id = excluded.player_id,
            status = cast('active' as membership_status_enum),
            billing_type = cast(:billing_type as billing_type_enum),
            joined_at = coalesce(public.group_members.joined_at, now()),
            updated_at = now()
    """), {
        'gid': invite['group_id'],
        'uid': principal.user_id,
        'pid': identity['player_id'],
        'billing_type': billing_type,
    })

    db.commit()
    return {"status": "accepted", "group_id": invite['group_id']}


@router.post("/v2/groups/invites/{invite_id}/reject")
def reject_invite(invite_id: str,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    current_email = db.execute(text("""
        select lower(email) as email
        from public.users
        where id = cast(:uid as uuid)
        limit 1
    """), {'uid': principal.user_id}).scalar()

    invite = db.execute(text("""
        select id::text as id, lower(invited_email) as invited_email, status::text as status
        from public.group_invitations
        where id = cast(:iid as uuid)
        limit 1
    """), {'iid': invite_id}).mappings().first()
    if not invite:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    if not current_email or current_email != invite['invited_email']:
        raise HTTPException(status_code=403, detail="Este convite não pertence ao utilizador autenticado.")
    if invite['status'] in ('accepted', 'rejected', 'removed'):
        raise HTTPException(status_code=400, detail="Convite já processado.")

    invitation_cols = _table_columns(db, 'group_invitations')
    if 'responded_at' in invitation_cols:
        db.execute(text("""
            update public.group_invitations
            set status = cast('rejected' as membership_status_enum),
                responded_at = now()
            where id = cast(:iid as uuid)
        """), {'iid': invite_id})
    else:
        db.execute(text("""
            update public.group_invitations
            set status = cast('rejected' as membership_status_enum)
            where id = cast(:iid as uuid)
        """), {'iid': invite_id})
    db.commit()
    return {"status": "rejected"}


# ═══════════════════════════════════════════════════════════════════════
# NOTIFICATION SETTINGS
# ═══════════════════════════════════════════════════════════════════════

class NotificationSettingsOut(BaseModel):
    matches_enabled: bool = True
    finance_enabled: bool = True
    announcements_enabled: bool = True
    comments_enabled: bool = True
    invites_enabled: bool = True
    fines_enabled: bool = True
    push_enabled: bool = True
    push_matches_enabled: bool = True
    push_finance_enabled: bool = True
    push_announcements_enabled: bool = True
    push_comments_enabled: bool = True
    push_invites_enabled: bool = True
    push_fines_enabled: bool = True


@router.get("/v2/users/me/notification-settings", response_model=NotificationSettingsOut)
def get_notification_settings(principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    row = db.execute(text("""
        select matches_enabled, finance_enabled, announcements_enabled, comments_enabled,
               invites_enabled, fines_enabled, push_enabled,
               push_matches_enabled, push_finance_enabled, push_announcements_enabled,
               push_comments_enabled, push_invites_enabled, push_fines_enabled
        from public.notification_settings_v2
        where user_id = cast(:uid as uuid) limit 1
    """), {'uid': principal.user_id}).mappings().first()
    if not row:
        return NotificationSettingsOut()
    return NotificationSettingsOut(**dict(row))


@router.put("/v2/users/me/notification-settings", response_model=NotificationSettingsOut)
def update_notification_settings(payload: dict,
                                 principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                                 db: Session = Depends(get_db_session)):
    allowed = {'matches_enabled', 'finance_enabled', 'announcements_enabled', 'comments_enabled',
               'invites_enabled', 'fines_enabled', 'push_enabled', 'push_matches_enabled',
               'push_finance_enabled', 'push_announcements_enabled', 'push_comments_enabled',
               'push_invites_enabled', 'push_fines_enabled'}
    filtered = {k: v for k, v in payload.items() if k in allowed and isinstance(v, bool)}
    if not filtered:
        return get_notification_settings(principal, db)
    # Upsert
    set_clause = ', '.join(f"{k} = :{k}" for k in filtered)
    cols = ', '.join(filtered.keys())
    vals = ', '.join(f":{k}" for k in filtered.keys())
    params = {**filtered, 'uid': principal.user_id}
    db.execute(text(f"""
        insert into public.notification_settings_v2 (user_id, {cols})
        values (cast(:uid as uuid), {vals})
        on conflict (user_id) do update set {set_clause}, updated_at = now()
    """), params)
    db.commit()
    return get_notification_settings(principal, db)


# ═══════════════════════════════════════════════════════════════════════
# PUSH TOKEN
# ═══════════════════════════════════════════════════════════════════════

class PushTokenPayload(BaseModel):
    token: str = Field(..., min_length=1)
    platform: str = "unknown"


@router.put("/v2/users/me/push-token")
def update_push_token(payload: PushTokenPayload,
                      principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                      db: Session = Depends(get_db_session)):
    db.execute(text("""
        insert into public.user_push_tokens_v2 (user_id, token, platform)
        values (cast(:uid as uuid), :token, :platform)
        on conflict (user_id, token) do update set platform = :platform, updated_at = now()
    """), {'uid': principal.user_id, 'token': payload.token, 'platform': payload.platform})
    db.commit()
    return {"ok": True}
