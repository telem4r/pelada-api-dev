from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Group, GroupMember, User, Player

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"




def _get_primary_player_id(db: Session, user_id: int) -> int:
    p = (
        db.query(Player)
        .filter(Player.owner_id == user_id)
        .order_by(Player.id.asc())
        .first()
    )
    if not p:
        raise HTTPException(status_code=400, detail="User não possui Player (base inconsistente)")
    return p.id


def _norm(v: str | None) -> str:
    return (v or "").strip().lower()


def get_group_member(db: Session, group_id: str, user_id: int) -> tuple[User, GroupMember]:
    """
    Compatibilidade com código antigo.
    Retorna (user, membership) do usuário no grupo.

    ✅ DB atual: group_members possui user_id (não player_id)
    ✅ group_id é UUID string (varchar(36))
    ✅ Exige membership ACTIVE (pra bater com a regra usada no app)
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    player_id = _get_primary_player_id(db, user_id)

    membership = (
        db.query(GroupMember)
        .filter(
            GroupMember.group_id == group_id,
            GroupMember.player_id == player_id,
        )
        .order_by(GroupMember.id.asc())
        .first()
    )

    if not membership:
        membership = (
            db.query(GroupMember)
            .filter(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id,
            )
            .order_by(GroupMember.id.asc())
            .first()
        )
    if not membership:
        raise HTTPException(status_code=403, detail="Você não é membro deste grupo")

    if _norm(membership.status) != "active":
        raise HTTPException(status_code=403, detail="Você não é membro ativo deste grupo")

    # normaliza (sem alterar DB)
    membership.role = _norm(membership.role) or ROLE_MEMBER
    membership.status = _norm(membership.status) or "pending"

    return user, membership


def get_user_role_in_group(db: Session, group_id: str, user_id: int) -> str:
    """
    Retorna a role do usuário no grupo (owner/admin/member) ou "" se não houver vínculo.
    """
    player_id = _get_primary_player_id(db, user_id)

    gm = (
        db.query(GroupMember)
        .filter(
            GroupMember.group_id == group_id,
            GroupMember.player_id == player_id,
        )
        .order_by(GroupMember.id.asc())
        .first()
    )

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
    if gm:
        return _norm(gm.role)

    # fallback: se por algum motivo não existe membership, mas é owner_id
    g = db.query(Group).filter(Group.id == group_id).first()
    if g and g.owner_id == user_id:
        return ROLE_OWNER

    return ""


def require_admin_or_owner(db: Session, group_id: str, user_id: int):
    role = get_user_role_in_group(db, group_id, user_id)
    if role not in (ROLE_OWNER, ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="Sem permissão para esta ação.")


def require_owner(db: Session, group_id: str, user_id: int):
    role = get_user_role_in_group(db, group_id, user_id)
    if role != ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Apenas o dono do grupo pode executar esta ação.")
