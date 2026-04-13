from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.core.supabase_storage import (
    build_group_avatar_path,
    build_player_avatar_path,
    ensure_avatar_content_type,
    normalize_avatar_value,
    resolve_avatar_url,
    upload_avatar_bytes,
)

router = APIRouter(prefix="/v2/avatars", tags=["Avatars V2"])

MAX_AVATAR_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def _validate_avatar_content(content: bytes) -> None:
    if len(content) > MAX_AVATAR_FILE_SIZE:
        raise HTTPException(status_code=400, detail="Ficheiro de avatar demasiado grande. Máximo: 5MB")
    if content.startswith(b"\xff\xd8\xff"):
        return
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return
    raise HTTPException(status_code=400, detail="Conteúdo de avatar inválido. Envie JPEG, PNG ou WEBP válidos.")


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class PresignRequest(BaseModel):
    content_type: str = "image/jpeg"
    filename: str = "avatar.jpg"


class PresignResponse(BaseModel):
    key: str
    upload_url: str
    read_url: str


class AvatarUrlPayload(BaseModel):
    avatar_url: Optional[str] = None


def _ensure_group_admin(db: Session, *, group_id: str, user_id: str) -> None:
    row = db.execute(
        text(
            """
            select gm.role::text as role, gm.status::text as status
            from public.group_members gm
            where gm.group_id = cast(:gid as uuid)
              and gm.user_id = cast(:uid as uuid)
            limit 1
            """
        ),
        {"gid": group_id, "uid": user_id},
    ).mappings().first()
    if not row or row.get("status") != "active":
        raise HTTPException(status_code=403, detail="Você ainda não é membro ativo deste grupo.")
    if row.get("role") not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Sem permissão para alterar escudo do grupo")


@router.post("/presign", response_model=PresignResponse)
def presign_user_avatar(payload: PresignRequest,
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    content_type = ensure_avatar_content_type(payload.content_type)
    key = build_player_avatar_path(principal.user_id, payload.filename, content_type)
    read_url = resolve_avatar_url(key)
    return PresignResponse(key=key, upload_url="", read_url=read_url or "")


@router.post("/me/upload")
async def upload_user_avatar(file: UploadFile = File(...),
                             principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                             db: Session = Depends(get_db_session)):
    content_type = ensure_avatar_content_type(file.content_type)
    content = await file.read()
    _validate_avatar_content(content)
    key = build_player_avatar_path(principal.user_id, file.filename, content_type)
    upload_avatar_bytes(path=key, content=content, content_type=content_type)
    db.execute(text("""
        update public.users set avatar_url = :url, updated_at = now()
        where id = cast(:uid as uuid)
    """), {'url': key, 'uid': principal.user_id})
    db.execute(text("""
        update public.players set avatar_url = :url, updated_at = now()
        where user_id = cast(:uid as uuid)
    """), {'url': key, 'uid': principal.user_id})
    db.commit()
    return {"ok": True, "avatar_url": resolve_avatar_url(key), "stored_avatar": key}


@router.put("/me")
def save_user_avatar(payload: AvatarUrlPayload,
                     principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                     db: Session = Depends(get_db_session)):
    avatar_value = normalize_avatar_value(payload.avatar_url)
    db.execute(text("""
        update public.users set avatar_url = :url, updated_at = now()
        where id = cast(:uid as uuid)
    """), {'url': avatar_value, 'uid': principal.user_id})
    db.execute(text("""
        update public.players set avatar_url = :url, updated_at = now()
        where user_id = cast(:uid as uuid)
    """), {'url': avatar_value, 'uid': principal.user_id})
    db.commit()
    return {"avatar_url": resolve_avatar_url(avatar_value)}


@router.post("/groups/{group_id}/presign", response_model=PresignResponse)
def presign_group_avatar(group_id: str, payload: PresignRequest,
                         principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                         db: Session = Depends(get_db_session)):
    _ensure_group_admin(db, group_id=group_id, user_id=principal.user_id)
    content_type = ensure_avatar_content_type(payload.content_type)
    key = build_group_avatar_path(group_id, payload.filename, content_type)
    read_url = resolve_avatar_url(key)
    return PresignResponse(key=key, upload_url="", read_url=read_url or "")


@router.post("/groups/{group_id}/upload")
async def upload_group_avatar(group_id: str,
                              file: UploadFile = File(...),
                              principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    _ensure_group_admin(db, group_id=group_id, user_id=principal.user_id)
    content_type = ensure_avatar_content_type(file.content_type)
    content = await file.read()
    _validate_avatar_content(content)
    key = build_group_avatar_path(group_id, file.filename, content_type)
    upload_avatar_bytes(path=key, content=content, content_type=content_type)
    db.execute(text("""
        update public.groups set avatar_url = :url, updated_at = now()
        where id = cast(:gid as uuid)
    """), {'url': key, 'gid': group_id})
    db.commit()
    return {"ok": True, "avatar_url": resolve_avatar_url(key), "stored_avatar": key}


@router.put("/groups/{group_id}")
def save_group_avatar(group_id: str, payload: AvatarUrlPayload,
                      principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                      db: Session = Depends(get_db_session)):
    _ensure_group_admin(db, group_id=group_id, user_id=principal.user_id)
    avatar_value = normalize_avatar_value(payload.avatar_url)
    db.execute(text("""
        update public.groups set avatar_url = :url, updated_at = now()
        where id = cast(:gid as uuid)
    """), {'url': avatar_value, 'gid': group_id})
    db.commit()
    return {"avatar_url": resolve_avatar_url(avatar_value)}
