from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.core.api_errors import api_error
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.avatars_routes import resolve_avatar_url
from app.models import GroupJoinRequest, GroupMember
from app.permissions import get_group_member, get_user_primary_player
from app.repositories.groups import (
    get_group_member_by_player_id,
    get_group_member_by_user_id,
    get_group_or_none,
    get_join_request_by_group_and_player,
    get_join_request_by_id,
    list_group_members_with_relations,
    list_pending_join_requests,
)


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


def _require_membership(db: Session, group_id: str, user_id: int) -> GroupMember:
    _, member = get_group_member(db, group_id, user_id)
    return member


def _require_admin_or_owner(member: GroupMember) -> None:
    if _norm_role(getattr(member, "role", None)) not in {"owner", "admin"}:
        raise api_error(403, code="admin_permission_required", message="Somente admin ou owner podem realizar esta ação.")


def _require_owner(member: GroupMember) -> None:
    if _norm_role(getattr(member, "role", None)) != "owner":
        raise api_error(403, code="owner_permission_required", message="Somente o owner pode realizar esta ação.")


def _get_target_member_or_404(db: Session, group_id: str, member_user_id: int) -> GroupMember:
    gm = get_group_member_by_user_id(db, group_id, member_user_id)
    if not gm:
        raise api_error(404, code="group_member_not_found", message="Membro não encontrado.")
    return gm


def _ensure_manageable_target(action: str, actor: GroupMember, target: GroupMember, actor_user_id: int) -> None:
    actor_role = _norm_role(getattr(actor, "role", None))
    target_role = _norm_role(getattr(target, "role", None))

    if target_role == "owner":
        raise api_error(400, code="owner_protected", message="Não é possível alterar ou remover o owner do grupo.")

    if actor_user_id == getattr(target, "user_id", None) and action in {"remove", "role"}:
        if action == "remove":
            raise api_error(400, code="self_remove_not_allowed", message="Use a ação de sair do grupo para remover a si mesmo.")
        raise api_error(400, code="self_role_change_not_allowed", message="Não é possível alterar a própria permissão por esta ação.")

    if actor_role == "admin" and target_role == "admin":
        if action == "remove":
            raise api_error(403, code="owner_required_remove_admin", message="Somente o owner pode remover outro administrador.")
        if action == "billing":
            raise api_error(403, code="owner_required_billing_admin", message="Somente o owner pode alterar o tipo de cobrança de outro administrador.")
        if action == "skill":
            raise api_error(403, code="owner_required_skill_admin", message="Somente o owner pode alterar a skill de outro administrador.")


def serialize_group_member(member: GroupMember, can_view_skill: bool = True) -> Dict[str, Any]:
    user = getattr(member, "user", None)
    player = getattr(member, "player", None)

    name = None
    avatar_url = None
    birth_date: Optional[date] = None
    position = None
    preferred_foot = None
    birth_city = None
    birth_state = None
    birth_country = None

    if user is not None:
        first = getattr(user, "first_name", None)
        last = getattr(user, "last_name", None)
        if isinstance(first, str) or isinstance(last, str):
            merged = f"{(first or '').strip()} {(last or '').strip()}".strip()
            name = merged or None
        if not name:
            raw_name = getattr(user, "name", None)
            if isinstance(raw_name, str) and raw_name.strip():
                name = raw_name.strip()

        avatar = getattr(user, "avatar_url", None)
        if isinstance(avatar, str) and avatar.strip():
            avatar_url = resolve_avatar_url(avatar.strip())
        birth_date = getattr(user, "birth_date", None)
        birth_city = getattr(user, "birth_city", None)
        birth_state = getattr(user, "birth_state", None)
        birth_country = getattr(user, "birth_country", None)

        if not position:
            raw_position = getattr(user, "position", None)
            if isinstance(raw_position, str) and raw_position.strip():
                position = raw_position.strip()
        if not preferred_foot:
            raw_foot = getattr(user, "preferred_foot", None)
            if isinstance(raw_foot, str) and raw_foot.strip():
                preferred_foot = raw_foot.strip()

    if player is not None:
        raw_position = getattr(player, "position", None)
        if isinstance(raw_position, str) and raw_position.strip():
            position = raw_position.strip()
        raw_foot = getattr(player, "preferred_foot", None)
        if isinstance(raw_foot, str) and raw_foot.strip():
            preferred_foot = raw_foot.strip()

    return {
        "id": member.id,
        "group_id": member.group_id,
        "user_id": member.user_id,
        "player_id": member.player_id,
        "role": member.role,
        "status": member.status,
        "billing_type": getattr(member, "billing_type", None),
        "skill_rating": int(getattr(member, "skill_rating", 3) or 3) if can_view_skill else None,
        "created_at": member.created_at,
        "updated_at": member.updated_at,
        "profile": {"name": name, "avatar_url": avatar_url, "birth_city": birth_city, "birth_state": birth_state, "birth_country": birth_country},
        "player": {
            "birth_date": birth_date,
            "position": position,
            "preferred_foot": preferred_foot,
        },
    }


def list_members(db: Session, group_id: str, current_user_id: int) -> List[Dict[str, Any]]:
    me = _require_membership(db, group_id, current_user_id)
    can_view_skill = _norm_role(getattr(me, "role", None)) in {"owner", "admin"}
    members = list_group_members_with_relations(db, group_id)
    return [serialize_group_member(member, can_view_skill=can_view_skill) for member in members]


def set_member_role(db: Session, group_id: str, member_user_id: int, role: str, current_user_id: int) -> Dict[str, Any]:
    me = _require_membership(db, group_id, current_user_id)
    _require_owner(me)

    role = _norm_role(role)
    if role not in {"admin", "member"}:
        raise api_error(400, code="invalid_member_role", message="Permissão inválida. Use admin ou member.")

    gm = _get_target_member_or_404(db, group_id, member_user_id)
    _ensure_manageable_target("role", me, gm, current_user_id)

    gm.role = role
    db.add(gm)
    db.commit()
    db.refresh(gm)
    return serialize_group_member(gm)


def remove_member(db: Session, group_id: str, member_user_id: int, current_user_id: int) -> Dict[str, bool]:
    me = _require_membership(db, group_id, current_user_id)
    _require_admin_or_owner(me)

    target = _get_target_member_or_404(db, group_id, member_user_id)
    _ensure_manageable_target("remove", me, target, current_user_id)

    try:
        db.delete(target)
        db.commit()
    except IntegrityError:
        db.rollback()
        target.status = "left"
        db.add(target)
        db.commit()
    return {"ok": True}


def get_my_membership(db: Session, group_id: str, current_user_id: int) -> Dict[str, Any]:
    _, me = get_group_member(db, group_id, current_user_id)
    normalized_status = _norm_role(me.status)
    return {
        "group_id": group_id,
        "user_id": current_user_id,
        "player_id": me.player_id,
        "role": _norm_role(me.role) or "member",
        "status": "member" if normalized_status == "active" else normalized_status or "pending",
        "membership_status": normalized_status or "active",
        "billing_type": getattr(me, "billing_type", None),
    }


def leave_group(db: Session, group_id: str, current_user_id: int) -> Dict[str, bool]:
    me = _require_membership(db, group_id, current_user_id)
    if _norm_role(me.role) == "owner":
        raise api_error(400, code="owner_cannot_leave", message="O owner não pode sair do grupo. Transfira a gestão antes de sair.")
    try:
        db.delete(me)
        db.commit()
    except IntegrityError:
        db.rollback()
        me.status = "left"
        db.add(me)
        db.commit()
    return {"ok": True}


def request_join_group(db: Session, group_id: str, current_user_id: int) -> GroupJoinRequest:
    existing_member = get_group_member_by_user_id(db, group_id, current_user_id)
    if existing_member and _norm_role(existing_member.status) in {"active", "pending"}:
        raise api_error(400, code="group_membership_already_exists", message="Você já participa do grupo ou já possui uma solicitação pendente.")

    player = get_user_primary_player(db, current_user_id)
    req = get_join_request_by_group_and_player(db, group_id, player.id)
    if req and _norm_role(req.status) == "pending":
        return req

    if not req:
        req = GroupJoinRequest(group_id=group_id, user_id=current_user_id, player_id=player.id, status="pending")
        db.add(req)
    else:
        req.status = "pending"
        req.user_id = current_user_id
        db.add(req)

    db.commit()
    db.refresh(req)
    return req


def serialize_join_request(req: GroupJoinRequest) -> Dict[str, Any]:
    user = getattr(req, 'user', None)
    player = getattr(req, 'player', None)

    name = None
    avatar_url = None
    if user is not None:
        first = getattr(user, 'first_name', None)
        last = getattr(user, 'last_name', None)
        if isinstance(first, str) or isinstance(last, str):
            merged = f"{(first or '').strip()} {(last or '').strip()}".strip()
            name = merged or None
        if not name:
            raw_name = getattr(user, 'name', None)
            if isinstance(raw_name, str) and raw_name.strip():
                name = raw_name.strip()
        avatar = getattr(user, 'avatar_url', None)
        if isinstance(avatar, str) and avatar.strip():
            avatar_url = resolve_avatar_url(avatar.strip())

    if not name and player is not None:
        raw_name = getattr(player, 'name', None)
        if isinstance(raw_name, str) and raw_name.strip():
            name = raw_name.strip()

    return {
        'id': req.id,
        'group_id': req.group_id,
        'user_id': req.user_id,
        'player_id': req.player_id,
        'status': req.status,
        'role': 'member',
        'created_at': req.created_at,
        'updated_at': req.updated_at,
        'player_name': name,
        'avatar_url': avatar_url,
        'profile': {'name': name, 'avatar_url': avatar_url},
    }


def list_join_requests_service(db: Session, group_id: str, current_user_id: int):
    me = _require_membership(db, group_id, current_user_id)
    _require_admin_or_owner(me)
    return [serialize_join_request(item) for item in list_pending_join_requests(db, group_id)]


def approve_join_request_service(db: Session, group_id: str, request_id: int, current_user_id: int) -> Dict[str, bool]:
    me = _require_membership(db, group_id, current_user_id)
    _require_admin_or_owner(me)

    req = get_join_request_by_id(db, group_id, request_id)
    if not req:
        raise api_error(404, code="join_request_not_found", message="Solicitação não encontrada.")
    if _norm_role(req.status) != "pending":
        return {"ok": True}

    gm = get_group_member_by_player_id(db, group_id, req.player_id)
    if not gm:
        gm = GroupMember(
            group_id=group_id,
            user_id=req.user_id,
            player_id=req.player_id,
            role="member",
            status="active",
        )
        db.add(gm)
    else:
        gm.status = "active"
        gm.user_id = req.user_id
        db.add(gm)

    req.status = "active"
    db.add(req)
    db.commit()
    return {"ok": True}


def reject_join_request_service(db: Session, group_id: str, request_id: int, current_user_id: int) -> Dict[str, bool]:
    me = _require_membership(db, group_id, current_user_id)
    _require_admin_or_owner(me)

    req = get_join_request_by_id(db, group_id, request_id)
    if not req:
        raise api_error(404, code="join_request_not_found", message="Solicitação não encontrada.")

    req.status = "rejected"
    db.add(req)
    db.commit()
    return {"ok": True}


def set_member_skill_rating_service(
    db: Session,
    group_id: str,
    member_user_id: int,
    skill_rating: int,
    current_user_id: int,
) -> Dict[str, Any]:
    me = _require_membership(db, group_id, current_user_id)
    _require_admin_or_owner(me)

    gm = _get_target_member_or_404(db, group_id, member_user_id)
    _ensure_manageable_target("skill", me, gm, current_user_id)

    value = int(skill_rating)
    if value < 1 or value > 5:
        raise api_error(400, code="invalid_skill_rating", message="A skill do membro deve estar entre 1 e 5.")

    gm.skill_rating = value
    db.add(gm)
    db.commit()
    db.refresh(gm)
    return serialize_group_member(gm)


def set_member_billing_type_service(
    db: Session,
    group_id: str,
    member_user_id: int,
    billing_type: str,
    current_user_id: int,
) -> Dict[str, Any]:
    me = _require_membership(db, group_id, current_user_id)
    _require_admin_or_owner(me)

    grp = get_group_or_none(db, group_id)
    if not grp:
        raise api_error(404, code="group_not_found", message="Grupo não encontrado.")
    if not _is_hybrid_group_type(getattr(grp, "group_type", None)):
        raise api_error(400, code="billing_type_requires_hybrid_group", message="O tipo de cobrança só pode ser alterado em grupos híbridos.")

    billing_type = _norm_role(billing_type)
    if billing_type not in {"monthly", "single"}:
        raise api_error(400, code="invalid_billing_type", message="Tipo de cobrança inválido. Use monthly ou single.")

    gm = _get_target_member_or_404(db, group_id, member_user_id)
    _ensure_manageable_target("billing", me, gm, current_user_id)

    gm.billing_type = billing_type
    db.add(gm)
    db.commit()
    db.refresh(gm)
    return serialize_group_member(gm)
