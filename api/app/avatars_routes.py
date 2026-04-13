from __future__ import annotations

import os
import uuid
from typing import Optional
from urllib.parse import urlparse

import boto3
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.supabase_storage import normalize_avatar_value as _normalize_supabase_avatar_storage, resolve_avatar_url as _resolve_supabase_avatar_url
from app.db import get_db
from app.models import User, Group, Player
from app.security import get_current_user as get_current_user_id  # ✅ retorna int (user_id)
from app.permissions import get_group_member

router = APIRouter(prefix="/avatars", tags=["avatars"])

S3_BUCKET = (os.getenv("S3_BUCKET_AVATARS") or "").strip()
S3_REGION = (os.getenv("S3_REGION") or "eu-west-1").strip()


def s3():
    return boto3.client("s3", region_name=S3_REGION)


def _extract_s3_key(value: Optional[str]) -> Optional[str]:
    raw = (value or '').strip()
    if not raw:
        return None
    if raw.startswith('avatars/'):
        return raw
    if raw.startswith('http://') or raw.startswith('https://'):
        try:
            parsed = urlparse(raw)
            path = (parsed.path or '').lstrip('/')
            if path.startswith('avatars/'):
                return path
        except Exception:
            return None
    return None


def resolve_avatar_url(value: Optional[str], expires_in: int = 3600) -> Optional[str]:
    raw = (value or '').strip()
    if not raw:
        return None
    supabase_resolved = _resolve_supabase_avatar_url(raw, expires_in=expires_in)
    if supabase_resolved and supabase_resolved != raw:
        return supabase_resolved
    key = _extract_s3_key(raw)
    if key and S3_BUCKET:
        try:
            return s3().generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': key},
                ExpiresIn=expires_in,
            )
        except Exception:
            pass
    return raw


def _normalize_avatar_storage(value: Optional[str]) -> Optional[str]:
    raw = (value or '').strip()
    if not raw:
        return None
    supabase_value = _normalize_supabase_avatar_storage(raw)
    if supabase_value and supabase_value != raw:
        return supabase_value
    key = _extract_s3_key(raw)
    return key or raw


def _current_user(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


class PresignIn(BaseModel):
    content_type: str = "image/jpeg"
    filename: Optional[str] = None


@router.post("/presign")
def presign(body: PresignIn, user: User = Depends(_current_user)):
    """
    Retorna uma URL pré-assinada para upload direto no S3.
    O app faz PUT no upload_url e depois chama PUT /avatars/me para salvar a public_url.
    """
    if not S3_BUCKET:
        raise HTTPException(status_code=500, detail="S3_BUCKET_AVATARS não configurado")

    # valida content-type (evita upload de coisas indevidas)
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if body.content_type not in allowed:
        raise HTTPException(status_code=400, detail="content_type inválido (use image/jpeg, image/png ou image/webp)")

    ext = ""
    if body.filename and "." in body.filename:
        ext = "." + body.filename.split(".")[-1].lower()

    key = f"avatars/u{user.id}/{uuid.uuid4().hex}{ext}"

    try:
        url = s3().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": key,
                "ContentType": body.content_type,
            },
            ExpiresIn=300,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar presign: {e}")

    public = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"
    read_url = resolve_avatar_url(key, expires_in=3600) or public
    return {"key": key, "upload_url": url, "public_url": public, "read_url": read_url, "expires_in": 300}


class AvatarSet(BaseModel):
    avatar_url: Optional[str] = None


@router.put("/me")
def set_avatar(
    body: AvatarSet,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    """
    Salva no banco a URL pública do avatar que foi enviado ao S3.
    """
    avatar_value = _normalize_avatar_storage(body.avatar_url)

    user.avatar_url = avatar_value
    db.add(user)
    try:
        player = db.query(Player).filter_by(user_id=user.id).first()
    except Exception:
        player = None
    if player is not None and hasattr(player, "avatar_url"):
        player.avatar_url = avatar_value
        db.add(player)
    db.commit()
    db.refresh(user)

    return {"ok": True, "avatar_url": resolve_avatar_url(user.avatar_url), "stored_avatar": user.avatar_url}


@router.post("/groups/{group_id}/presign")
def presign_group_avatar(
    group_id: str,
    body: PresignIn,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    if not S3_BUCKET:
        raise HTTPException(status_code=500, detail="S3_BUCKET_AVATARS não configurado")

    allowed = {"image/jpeg", "image/png", "image/webp"}
    if body.content_type not in allowed:
        raise HTTPException(status_code=400, detail="content_type inválido (use image/jpeg, image/png ou image/webp)")

    group, member = get_group_member(db, group_id=group_id, user_id=user.id)
    role = (getattr(member, "role", "") or "").strip().lower()
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Sem permissão para alterar escudo do grupo")

    ext = ""
    if body.filename and "." in body.filename:
        ext = "." + body.filename.split(".")[-1].lower()

    key = f"avatars/groups/{group_id}/{uuid.uuid4().hex}{ext}"

    try:
        url = s3().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": S3_BUCKET,
                "Key": key,
                "ContentType": body.content_type,
            },
            ExpiresIn=300,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar presign: {e}")

    public = f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"
    read_url = resolve_avatar_url(key, expires_in=3600) or public
    return {"key": key, "upload_url": url, "public_url": public, "read_url": read_url, "expires_in": 300}


class GroupAvatarSet(BaseModel):
    avatar_url: Optional[str] = None


@router.put("/groups/{group_id}")
def set_group_avatar(
    group_id: str,
    body: GroupAvatarSet,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    group, member = get_group_member(db, group_id=group_id, user_id=user.id)
    role = (getattr(member, "role", "") or "").strip().lower()
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Sem permissão para alterar escudo do grupo")

    group.avatar_url = _normalize_avatar_storage(body.avatar_url)
    db.add(group)
    db.commit()
    db.refresh(group)
    return {"ok": True, "avatar_url": resolve_avatar_url(group.avatar_url), "stored_avatar": group.avatar_url}
