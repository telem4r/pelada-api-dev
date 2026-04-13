from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Group, GroupMember, Player, User


def _norm(v: str | None) -> str:
    return (v or "").strip().lower()


def _ensure_player_for_user(db: Session, user_id: int) -> Player:
    """
    Regra do projeto: todo User é automaticamente um Player.
    Mantemos fallback para usuários antigos e bases inconsistentes.
    """
    p = (
        db.query(Player)
        .filter(Player.owner_id == user_id)
        .order_by(Player.id.asc())
        .first()
    )
    if p:
        return p

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=401, detail="User not found")

    name = (u.name or "").strip() or "Jogador"

    p = Player(
        owner_id=user_id,
        name=name,
        team_id=None,
        position=None,
        preferred_foot=None,
        rating=0,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def get_user_primary_player(db: Session, user_id: int) -> Player:
    return _ensure_player_for_user(db, user_id)


def get_group_member(db: Session, group_id: str, user_id: int) -> tuple[Group, GroupMember]:
    """
    Agora group_id é UUID string e membership é por group_members.player_id (canônico).
    Exige membro ATIVO.
    """
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    player = get_user_primary_player(db, user_id)

    gm = (
        db.query(GroupMember)
        .filter(
            GroupMember.group_id == group_id,
            GroupMember.player_id == player.id,
        )
        .order_by(GroupMember.id.asc())
        .first()
    )

    # fallback de compatibilidade (bases antigas): procura por user_id e corrige player_id
    if not gm:
        gm = (
            db.query(GroupMember)
            .filter(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id,
            )
            .order_by(GroupMember.id.asc())
            .first()
        )
        if gm and (getattr(gm, 'player_id', None) is None or gm.player_id != player.id):
            try:
                gm.player_id = player.id
                db.add(gm)
                db.commit()
                db.refresh(gm)
            except Exception:
                db.rollback()


    if not gm:
        raise HTTPException(status_code=403, detail="User is not a member of this group")

    if _norm(gm.status) != "active":
        raise HTTPException(status_code=403, detail="User is not an active member of this group")

    gm.role = _norm(gm.role) or "member"
    gm.status = _norm(gm.status) or "pending"

    return group, gm


def require_group_admin_or_owner(db: Session, group_id: str, user_id: int) -> tuple[Group, GroupMember]:
    group, gm = get_group_member(db, group_id, user_id)
    if _norm(gm.role) not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin/Owner permissions required")
    return group, gm


def require_group_owner(db: Session, group_id: str, user_id: int) -> tuple[Group, GroupMember]:
    group, gm = get_group_member(db, group_id, user_id)
    if _norm(gm.role) != "owner":
        raise HTTPException(status_code=403, detail="Owner permissions required")
    return group, gm


# Compatibilidade com nomes antigos
def require_group_admin(db: Session, group_id: str, user_id: int) -> tuple[Group, GroupMember]:
    return require_group_admin_or_owner(db, group_id, user_id)


def is_group_admin(db: Session, group_id: str, user_id: int) -> bool:
    try:
        _, gm = get_group_member(db, group_id, user_id)
        return _norm(gm.role) in ("owner", "admin")
    except HTTPException:
        return False


def is_group_member_active(db: Session, group_id: str, user_id: int) -> bool:
    try:
        _, gm = get_group_member(db, group_id, user_id)
        return _norm(gm.status) == "active"
    except HTTPException:
        return False
