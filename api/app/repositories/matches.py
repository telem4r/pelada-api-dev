from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models import Match, MatchGuestPlayer, MatchParticipant, ParticipantStatus


def require_match(db: Session, match_id: int) -> Match:
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return match


def participant_table(db: Session) -> Table:
    meta = MetaData()
    return Table("match_participants", meta, autoload_with=db.bind)


def guest_table(db: Session) -> Table:
    meta = MetaData()
    return Table("match_guests", meta, autoload_with=db.bind)


def participant_columns(db: Session) -> set[str]:
    return {c["name"] for c in inspect(db.bind).get_columns("match_participants")}


def guest_columns(db: Session) -> set[str]:
    return {c["name"] for c in inspect(db.bind).get_columns("match_guests")}


def list_match_participants(db: Session, match_id: int):
    return (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .order_by(
            MatchParticipant.status.asc(),
            MatchParticipant.waitlist_tier.asc(),
            MatchParticipant.queue_position.asc().nullslast(),
            MatchParticipant.id.asc(),
        )
        .all()
    )


def list_match_guests(db: Session, match_id: int):
    table = guest_table(db)
    stmt = select(table).where(table.c.match_id == match_id).order_by(table.c.id.asc())
    return db.execute(stmt).fetchall()


def get_match_participant(db: Session, match_id: int, player_id: int):
    return (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .filter(MatchParticipant.player_id == player_id)
        .first()
    )


def get_match_participant_by_id(db: Session, match_id: int, attendance_id: int):
    return (
        db.query(MatchParticipant)
        .filter(MatchParticipant.id == attendance_id)
        .filter(MatchParticipant.match_id == match_id)
        .first()
    )


def get_match_guest(db: Session, match_id: int, guest_id: int):
    orm_guest = (
        db.query(MatchGuestPlayer)
        .filter(MatchGuestPlayer.match_id == match_id)
        .filter(MatchGuestPlayer.id == guest_id)
        .first()
    )
    if orm_guest is not None:
        return orm_guest
    table = guest_table(db)
    row = db.execute(
        select(table).where(table.c.match_id == match_id).where(table.c.id == guest_id).limit(1)
    ).first()
    return row


def find_existing_participant(db: Session, match_id: int, player_id: int, user_id: int):
    q = db.query(MatchParticipant).filter(MatchParticipant.match_id == match_id)
    existing = q.filter(MatchParticipant.player_id == player_id).first()
    if existing:
        return existing
    cols = participant_columns(db)
    if "user_id" in cols:
        table = participant_table(db)
        row = db.execute(
            select(table.c.id)
            .where(table.c.match_id == match_id)
            .where(table.c.user_id == user_id)
            .limit(1)
        ).first()
        if row:
            return db.query(MatchParticipant).filter(MatchParticipant.id == int(row.id)).first()
    return None


def upsert_participant(
    db: Session,
    *,
    match_id: int,
    player_id: int,
    user_id: int,
    status: str,
    waitlist_tier: int,
    requires_approval: bool,
    queue_position: int | None,
    position: str | None = None,
):
    cols = participant_columns(db)
    table = participant_table(db)
    now = utc_now()
    existing = find_existing_participant(db, match_id, player_id, user_id)
    values: dict[str, object] = {}
    if "match_id" in cols:
        values["match_id"] = int(match_id)
    if "player_id" in cols:
        values["player_id"] = int(player_id)
    if "user_id" in cols:
        values["user_id"] = int(user_id)
    if "status" in cols:
        values["status"] = status
    if "waitlist_tier" in cols:
        values["waitlist_tier"] = int(waitlist_tier)
    if "requires_approval" in cols:
        values["requires_approval"] = bool(requires_approval)
    if "queue_position" in cols:
        values["queue_position"] = queue_position
    if "position" in cols:
        values["position"] = position
    if "updated_at" in cols:
        values["updated_at"] = now
    if existing:
        db.execute(table.update().where(table.c.id == int(existing.id)).values(**values))
        return find_existing_participant(db, match_id, player_id, user_id) or existing
    if "created_at" in cols:
        values["created_at"] = now
    db.execute(table.insert().values(**values))
    return find_existing_participant(db, match_id, player_id, user_id)


def delete_member_presence(db: Session, match_id: int, player_id: int) -> bool:
    table = participant_table(db)
    deleted = db.execute(
        table.delete().where(table.c.match_id == int(match_id)).where(table.c.player_id == int(player_id))
    )
    return bool(getattr(deleted, "rowcount", 0))


def delete_guest_presence(db: Session, match_id: int, guest_id: int) -> bool:
    table = guest_table(db)
    deleted = db.execute(
        table.delete().where(table.c.match_id == int(match_id)).where(table.c.id == int(guest_id))
    )
    return bool(getattr(deleted, "rowcount", 0))


def confirmed_count(db: Session, match_id: int) -> int:
    return (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .filter(MatchParticipant.status == ParticipantStatus.confirmed.value)
        .count()
    )


def total_confirmed_count(db: Session, match_id: int) -> int:
    parts_count = confirmed_count(db, match_id)
    cols = guest_columns(db)
    if "status" not in cols:
        return int(parts_count)
    table = guest_table(db)
    guest_count = db.execute(
        select(table.c.id)
        .where(table.c.match_id == match_id)
        .where(table.c.status == ParticipantStatus.confirmed.value)
    ).fetchall()
    return int(parts_count) + len(guest_count)


def capacity_ok(match: Match, confirmed: int) -> bool:
    return (match.player_limit or 0) <= 0 or confirmed < (match.player_limit or 0)


def next_queue_position(db: Session, match_id: int, tier: int) -> int:
    last = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .filter(MatchParticipant.status == ParticipantStatus.waitlist.value)
        .filter(MatchParticipant.waitlist_tier == tier)
        .order_by(MatchParticipant.queue_position.desc().nullslast(), MatchParticipant.id.desc())
        .first()
    )
    if last and last.queue_position is not None:
        return int(last.queue_position) + 1
    return 1
