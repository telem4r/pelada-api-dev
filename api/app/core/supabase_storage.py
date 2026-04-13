from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from fastapi import HTTPException

from app.core.config import settings

SUPABASE_STORAGE_BUCKET = (
    os.getenv("SUPABASE_STORAGE_BUCKET_AVATARS")
    or os.getenv("SUPABASE_STORAGE_BUCKET")
    or "borafut-avatars-prod"
).strip()

_ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
_AVATAR_FIELD_NAMES = {
    "avatar_url",
    "player_avatar_url",
    "user_avatar_url",
    "actor_avatar_url",
    "group_avatar_url",
}


def _default_signed_url_ttl() -> int:
    raw = (os.getenv("SUPABASE_AVATAR_SIGNED_URL_TTL") or "86400").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 86400
    return value if value > 0 else 86400


_DEFAULT_SIGNED_URL_TTL = _default_signed_url_ttl()


def ensure_avatar_content_type(content_type: str | None) -> str:
    value = (content_type or "").strip().lower() or "image/jpeg"
    if value not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="content_type inválido (use image/jpeg, image/png ou image/webp)")
    return value


def _extension_for(filename: str | None, content_type: str) -> str:
    name = (filename or "").strip().lower()
    if "." in name:
        ext = "." + name.rsplit(".", 1)[-1]
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            return ext
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".jpg")


def build_player_avatar_path(user_id: str, filename: str | None, content_type: str) -> str:
    ext = _extension_for(filename, content_type)
    stamp = int(time.time())
    suffix = uuid.uuid4().hex[:8]
    return f"players/{user_id}/avatar_{stamp}_{suffix}{ext}"


def build_group_avatar_path(group_id: str, filename: str | None, content_type: str) -> str:
    ext = _extension_for(filename, content_type)
    stamp = int(time.time())
    suffix = uuid.uuid4().hex[:8]
    return f"groups/{group_id}/avatar_{stamp}_{suffix}{ext}"


def _require_config() -> tuple[str, str, str]:
    supabase_url = (settings.supabase_url or "").rstrip("/")
    service_key = (settings.supabase_service_role_key or "").strip()
    if not supabase_url or not service_key:
        raise HTTPException(status_code=500, detail="Supabase Storage não configurado no backend")
    return supabase_url, service_key, SUPABASE_STORAGE_BUCKET


def _request(method: str, url: str, *, body: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
    req = Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read()
            if not raw:
                return None
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(raw.decode("utf-8"))
            return raw
    except HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=500, detail=f"Erro no Supabase Storage: {payload or exc.reason}")
    except URLError as exc:
        raise HTTPException(status_code=500, detail=f"Falha de comunicação com Supabase Storage: {exc}")


def upload_avatar_bytes(*, path: str, content: bytes, content_type: str) -> None:
    supabase_url, service_key, bucket = _require_config()
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    url = f"{supabase_url}/storage/v1/object/{quote(bucket, safe='')}/{encoded_path}"
    _request(
        "POST",
        url,
        body=content,
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Content-Type": content_type,
            "x-upsert": "true",
            "Cache-Control": "3600",
        },
    )


def create_signed_avatar_url(path: str, *, expires_in: int = 3600) -> str:
    supabase_url, service_key, bucket = _require_config()
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    url = f"{supabase_url}/storage/v1/object/sign/{quote(bucket, safe='')}/{encoded_path}"
    payload = json.dumps({"expiresIn": int(expires_in)}).encode("utf-8")
    data = _request(
        "POST",
        url,
        body=payload,
        headers={
            "Authorization": f"Bearer {service_key}",
            "apikey": service_key,
            "Content-Type": "application/json",
        },
    ) or {}
    signed_url = data.get("signedURL") or data.get("signedUrl")
    if not signed_url:
        raise HTTPException(status_code=500, detail="Supabase Storage não retornou signed URL")
    if signed_url.startswith("http://") or signed_url.startswith("https://"):
        return signed_url
    if not signed_url.startswith("/"):
        signed_url = "/" + signed_url
    return f"{supabase_url}/storage/v1{signed_url}"


def _extract_supabase_key(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("players/") or raw.startswith("groups/"):
        return raw
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return None
    path = (parsed.path or "").strip()
    bucket = SUPABASE_STORAGE_BUCKET
    prefixes = [
        f"/storage/v1/object/sign/{bucket}/",
        f"/storage/v1/object/authenticated/{bucket}/",
        f"/storage/v1/object/public/{bucket}/",
        f"/object/sign/{bucket}/",
        f"/object/authenticated/{bucket}/",
        f"/object/public/{bucket}/",
    ]
    for prefix in prefixes:
        if path.startswith(prefix):
            suffix = path[len(prefix):]
            suffix = unquote(suffix.split("?", 1)[0]).strip("/")
            return suffix or None
    return None


def normalize_avatar_value(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    return _extract_supabase_key(raw) or raw


def resolve_avatar_url(value: str | None, *, expires_in: int = _DEFAULT_SIGNED_URL_TTL) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    key = _extract_supabase_key(raw)
    if key:
        return create_signed_avatar_url(key, expires_in=expires_in)
    return raw


def resolve_avatar_fields(payload: Any) -> Any:
    if isinstance(payload, list):
        return [resolve_avatar_fields(item) for item in payload]
    if isinstance(payload, dict):
        resolved: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _AVATAR_FIELD_NAMES:
                try:
                    resolved[key] = resolve_avatar_url(value)
                except Exception:
                    resolved[key] = None
            else:
                resolved[key] = resolve_avatar_fields(value)
        return resolved
    return payload
