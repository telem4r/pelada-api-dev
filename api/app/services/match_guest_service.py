from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now, utc_today
from app.models import Group, GroupFinancialEntry, Match, MatchGuestPlayer, ParticipantStatus
from app.repositories.matches import guest_table, guest_columns
from app.services.finance_snapshot_service import rebuild_snapshot


def _normalize_match_position(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if raw in {"gol", "goleiro", "goal", "goalkeeper", "keeper"}:
        return "goalkeeper"
    if raw in {"linha", "line", "player"}:
        return "line"
    return None


def _guest_row_to_out(row, *, fallback_match_id: int | None = None) -> dict:
    data = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
    now = utc_now()
    raw_status = (
        data.get("status")
        or data.get("presence")
        or data.get("list")
        or data.get("list_type")
        or ParticipantStatus.confirmed.value
    )
    status = str(raw_status).strip().lower()
    if status in {"waiting", "wait", "queue"}:
        status = ParticipantStatus.waitlist.value
    elif status not in {ParticipantStatus.confirmed.value, ParticipantStatus.waitlist.value, ParticipantStatus.rejected.value}:
        status = ParticipantStatus.confirmed.value
    raw_skill = data.get("skill_rating")
    if raw_skill is None:
        raw_skill = data.get("rating")
    return {
        "id": int(data.get("id")),
        "match_id": int(data.get("match_id") or fallback_match_id or 0),
        "name": str(data.get("name") or "Convidado"),
        "position": data.get("position"),
        "skill_rating": int(raw_skill or 3),
        "status": status,
        "arrived": bool(data.get("arrived", False)),
        "no_show": bool(data.get("no_show", False)),
        "no_show_justified": bool(data.get("no_show_justified", False)),
        "no_show_reason": data.get("no_show_reason"),
        "created_at": data.get("created_at") or now,
        "updated_at": data.get("updated_at") or now,
    }


def list_guests_for_match(db: Session, *, match_id: int) -> list[dict]:
    table = guest_table(db)
    rows = db.execute(select(table).where(table.c.match_id == match_id).order_by(table.c.id.asc())).fetchall()
    return [_guest_row_to_out(row, fallback_match_id=match_id) for row in rows]


def add_guest_to_match(
    db: Session,
    *,
    match: Match,
    current_user_id: int,
    name: str,
    position: str | None,
    skill_rating: int,
    presence: str | None,
) -> dict:
    cols = guest_columns(db)
    table = guest_table(db)
    now = utc_now()
    requested_presence = (presence or "confirmed").strip().lower()
    status_value = ParticipantStatus.waitlist.value if requested_presence in {"waiting", "wait", "waitlist", "queue"} else ParticipantStatus.confirmed.value
    legacy_presence = "waiting" if status_value == ParticipantStatus.waitlist.value else "confirmed"
    values: dict[str, object] = {}
    if "match_id" in cols:
        values["match_id"] = int(match.id)
    if "group_id" in cols:
        values["group_id"] = str(match.group_id)
    if "created_by_user_id" in cols:
        values["created_by_user_id"] = int(current_user_id)
    if "user_id" in cols:
        values["user_id"] = int(current_user_id)
    if "owner_id" in cols:
        values["owner_id"] = int(current_user_id)
    if "name" in cols:
        values["name"] = name.strip()
    if "position" in cols:
        values["position"] = _normalize_match_position(position) if position else None
    if "skill_rating" in cols:
        values["skill_rating"] = int(skill_rating)
    if "rating" in cols:
        values["rating"] = int(skill_rating)
    if "status" in cols:
        values["status"] = status_value
    if "presence" in cols:
        values["presence"] = legacy_presence
    if "list" in cols:
        values["list"] = legacy_presence
    if "list_type" in cols:
        values["list_type"] = legacy_presence
    if "arrived" in cols:
        values["arrived"] = False
    if "no_show" in cols:
        values["no_show"] = False
    if "no_show_justified" in cols:
        values["no_show_justified"] = False
    if "no_show_reason" in cols:
        values["no_show_reason"] = None
    if "created_at" in cols:
        values["created_at"] = now
    if "updated_at" in cols:
        values["updated_at"] = now
    try:
        result = db.execute(table.insert().values(**values))
        inserted_id = getattr(result, "inserted_primary_key", [None])[0]
        row = None
        if inserted_id is not None:
            row = db.execute(select(table).where(table.c.id == int(inserted_id)).limit(1)).first()
        if row is None:
            row = db.execute(select(table).where(table.c.match_id == match.id).order_by(table.c.id.desc()).limit(1)).first()
        db.commit()
        if row is None:
            raise HTTPException(status_code=500, detail="Convidado criado mas não encontrado para retorno")
        return _guest_row_to_out(row, fallback_match_id=match.id)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Erro ao adicionar convidado: {exc.orig}")


def update_guest_for_match(
    db: Session,
    *,
    match_id: int,
    guest_id: int,
    arrived: Optional[bool],
    status: Optional[str],
    position: Optional[str] = None,
) -> dict:
    guest = (
        db.query(MatchGuestPlayer)
        .filter(MatchGuestPlayer.match_id == match_id)
        .filter(MatchGuestPlayer.id == guest_id)
        .first()
    )
    if guest is None:
        table = guest_table(db)
        row = db.execute(select(table).where(table.c.match_id == match_id).where(table.c.id == guest_id).limit(1)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Guest not found")
        cols = guest_columns(db)
        values: dict[str, object] = {}
        if status is not None:
            if status not in {ParticipantStatus.confirmed.value, ParticipantStatus.waitlist.value, ParticipantStatus.rejected.value}:
                raise HTTPException(status_code=422, detail="status inválido")
            if "status" in cols:
                values["status"] = status
        if arrived is not None and "arrived" in cols:
            values["arrived"] = bool(arrived)
        if position is not None and "position" in cols:
            values["position"] = _normalize_match_position(position) if position is not None else None
        if "updated_at" in cols:
            values["updated_at"] = utc_now()
        if values:
            db.execute(table.update().where(table.c.id == guest_id).values(**values))
            db.commit()
        row = db.execute(select(table).where(table.c.id == guest_id).limit(1)).first()
        return _guest_row_to_out(row, fallback_match_id=match_id)
    if status is not None:
        if status not in {ParticipantStatus.confirmed.value, ParticipantStatus.waitlist.value, ParticipantStatus.rejected.value}:
            raise HTTPException(status_code=422, detail="status inválido")
        guest.status = status
    if arrived is not None:
        guest.arrived = bool(arrived)
    if position is not None:
        guest.position = _normalize_match_position(position) if position is not None else None
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return {
        "id": int(guest.id),
        "match_id": int(guest.match_id),
        "name": guest.name,
        "position": guest.position,
        "skill_rating": int(guest.skill_rating or 3),
        "status": guest.status,
        "arrived": bool(guest.arrived),
        "no_show": bool(getattr(guest, "no_show", False)),
        "no_show_justified": bool(getattr(guest, "no_show_justified", False)),
        "no_show_reason": getattr(guest, "no_show_reason", None),
        "created_at": guest.created_at,
        "updated_at": guest.updated_at,
    }


def delete_guest_from_match(db: Session, *, match_id: int, guest_id: int) -> None:
    guest = (
        db.query(MatchGuestPlayer)
        .filter(MatchGuestPlayer.match_id == match_id)
        .filter(MatchGuestPlayer.id == guest_id)
        .first()
    )
    if guest is not None:
        db.delete(guest)
        db.commit()
        return
    table = guest_table(db)
    deleted = db.execute(table.delete().where(table.c.match_id == match_id).where(table.c.id == guest_id))
    if not getattr(deleted, "rowcount", 0):
        db.rollback()
        raise HTTPException(status_code=404, detail="Guest not found")
    db.commit()


def set_guest_flags(
    db: Session,
    *,
    match_id: int,
    guest_id: int,
    arrived: Optional[bool] = None,
    position: Optional[str] = None,
):
    normalized_position = _normalize_match_position(position) if position is not None else None
    guest = (
        db.query(MatchGuestPlayer)
        .filter(MatchGuestPlayer.match_id == match_id)
        .filter(MatchGuestPlayer.id == guest_id)
        .first()
    )
    if guest is None:
        table = guest_table(db)
        cols = guest_columns(db)
        values: dict[str, object] = {}
        if arrived is not None and "arrived" in cols:
            values["arrived"] = bool(arrived)
        if normalized_position is not None and "position" in cols:
            values["position"] = normalized_position
        if arrived and "no_show" in cols:
            values["no_show"] = False
        if arrived and "no_show_justified" in cols:
            values["no_show_justified"] = False
        if arrived and "no_show_reason" in cols:
            values["no_show_reason"] = None
        if "updated_at" in cols:
            values["updated_at"] = utc_now()
        if values:
            db.execute(table.update().where(table.c.match_id == match_id).where(table.c.id == guest_id).values(**values))
        return values if values else None
    if arrived is not None:
        guest.arrived = bool(arrived)
    if normalized_position is not None:
        guest.position = normalized_position
    if bool(arrived):
        guest.no_show = False
        guest.no_show_justified = False
        guest.no_show_reason = None
    db.add(guest)
    return guest


def mark_guest_no_show(

    db: Session,
    *,
    match: Match,
    guest_id: int,
    justified: bool,
    reason: str | None,
    current_user_id: int,
) -> dict:
    guest = (
        db.query(MatchGuestPlayer)
        .filter(MatchGuestPlayer.match_id == match.id)
        .filter(MatchGuestPlayer.id == guest_id)
        .first()
    )
    if guest is None:
        table = guest_table(db)
        cols = guest_columns(db)
        row = db.execute(select(table).where(table.c.match_id == match.id).where(table.c.id == guest_id).limit(1)).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Guest not found")
        values: dict[str, object] = {}
        if "arrived" in cols:
            values["arrived"] = False
        if "no_show" in cols:
            values["no_show"] = True
        if "no_show_justified" in cols:
            values["no_show_justified"] = bool(justified)
        if "no_show_reason" in cols:
            values["no_show_reason"] = reason
        if "updated_at" in cols:
            values["updated_at"] = utc_now()
        db.execute(table.update().where(table.c.id == guest_id).values(**values))
        db.commit()
        return {"ok": True, "fine_created": False}
    guest.arrived = False
    guest.no_show = True
    guest.no_show_justified = bool(justified)
    guest.no_show_reason = reason
    db.add(guest)
    db.commit()
    return {"ok": True, "fine_created": False}
