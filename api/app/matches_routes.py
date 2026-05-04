from __future__ import annotations
from app.core.logging import configure_logging, log_event
logger = configure_logging()

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import AliasChoices, BaseModel, Field, root_validator
from sqlalchemy import MetaData, Table, inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Match,
    MatchDrawTeam,
    MatchParticipant,
    ParticipantStatus,
    Group,
    GroupMember,
    GroupFinancialEntry,
    Player,
    User,
    MatchGuestPlayer,
    MatchJoinRequest,
    MatchEvent,
    MatchStatus,
    JoinStatus,
)

from app.permissions import get_user_primary_player, get_group_member, require_group_admin
from app.security import get_current_user  # ✅ retorna user_id (int)
from app.communication_utils import dispatch_match_created
from app.core.api_errors import api_error
from app.core.time import utc_now
from app.services.match_guest_service import add_guest_to_match, delete_guest_from_match, list_guests_for_match, mark_guest_no_show as svc_mark_guest_no_show, update_guest_for_match
from app.services.match_presence_service import admin_mark_presence, approve_member_presence_entry, build_presence, confirm_presence_for_user, mark_member_no_show, remove_presence_entry
from app.services.match_waitlist_service import promote_waitlist_entries

router = APIRouter(prefix="/matches", tags=["Legacy - Matches"])

# ✅ Rotas oficiais dentro da hierarquia do grupo
group_router = APIRouter(prefix="/groups/{group_id}/matches", tags=["Groups - Matches"])


def _guest_table(db: Session) -> Table:
    """Reflete a tabela real do banco para evitar divergência entre ORM e schema legado."""
    meta = MetaData()
    return Table("match_guests", meta, autoload_with=db.bind)


def _guest_table_columns(db: Session) -> set[str]:
    return {c["name"] for c in inspect(db.bind).get_columns("match_guests")}


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


def _guest_rows_for_match(db: Session, match_id: int):
    table = _guest_table(db)
    stmt = select(table).where(table.c.match_id == match_id).order_by(table.c.id.asc())
    return db.execute(stmt).fetchall()




def _participant_table(db: Session) -> Table:
    """Reflete a tabela real para compatibilidade com schemas legados em produção."""
    meta = MetaData()
    return Table("match_participants", meta, autoload_with=db.bind)


def _participant_table_columns(db: Session) -> set[str]:
    return {c["name"] for c in inspect(db.bind).get_columns("match_participants")}


def _find_existing_participant_compat(db: Session, match_id: int, player_id: int, user_id: int):
    q = db.query(MatchParticipant).filter(MatchParticipant.match_id == match_id)

    existing = q.filter(MatchParticipant.player_id == player_id).first()
    if existing:
        return existing

    cols = _participant_table_columns(db)
    if "user_id" in cols:
        table = _participant_table(db)
        row = db.execute(
            select(table.c.id)
            .where(table.c.match_id == match_id)
            .where(table.c.user_id == user_id)
            .limit(1)
        ).first()
        if row:
            return db.query(MatchParticipant).filter(MatchParticipant.id == int(row.id)).first()

    return None


def _save_participant_compat(
    db: Session,
    *,
    match_id: int,
    player_id: int,
    user_id: int,
    status: str,
    waitlist_tier: int,
    requires_approval: bool,
    queue_position: int | None,
):
    """Insere/atualiza participante respeitando colunas legadas reais do banco."""
    cols = _participant_table_columns(db)
    table = _participant_table(db)
    now = utc_now()

    existing = _find_existing_participant_compat(db, match_id, player_id, user_id)
    values = {}

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
    if "updated_at" in cols:
        values["updated_at"] = now

    if existing:
        db.execute(table.update().where(table.c.id == int(existing.id)).values(**values))
    else:
        if "created_at" in cols:
            values["created_at"] = now
        db.execute(table.insert().values(**values))

    db.commit()
def _insert_guest_compat(db: Session, match: Match, payload: "GuestCreateIn", current_user_id: int) -> dict:
    """Insere convidado de forma compatível com schemas híbridos/legados.

    Problema real observado neste fluxo:
    - o frontend já envia nome + skill + presença corretamente
    - após sucesso ele recarrega a presença e ainda possui fallback local
    - portanto, quando o convidado não entra na lista, o gargalo quase sempre é o INSERT
      em `match_guests` (campos legados/sinónimos/NOT NULL em produção)

    Estratégia conservadora:
    - escrever TODOS os sinónimos conhecidos quando existirem
    - cobrir colunas legadas de autoria (`created_by_user_id`, `user_id`, `owner_id`)
    - cobrir rating/status/presence/list/list_type simultaneamente quando presentes
    - recuperar a linha inserida sem depender de um único mecanismo de retorno
    """
    cols = _guest_table_columns(db)
    table = _guest_table(db)
    now = utc_now()

    requested_presence = (payload.presence or "confirmed").strip().lower()
    status_value = (
        ParticipantStatus.waitlist.value
        if requested_presence in {"waiting", "wait", "waitlist", "queue"}
        else ParticipantStatus.confirmed.value
    )
    legacy_presence = "waiting" if status_value == ParticipantStatus.waitlist.value else "confirmed"

    clean_name = payload.name.strip()
    clean_position = payload.position.strip() if payload.position else None
    skill_value = int(payload.skill_rating)

    values = {}

    # chaves principais
    if "match_id" in cols:
        values["match_id"] = int(match.id)
    if "group_id" in cols:
        values["group_id"] = str(match.group_id)

    # autoria / rastreio legado
    if "created_by_user_id" in cols:
        values["created_by_user_id"] = int(current_user_id)
    if "user_id" in cols:
        values["user_id"] = int(current_user_id)
    if "owner_id" in cols:
        values["owner_id"] = int(current_user_id)

    # dados do convidado
    if "name" in cols:
        values["name"] = clean_name
    if "position" in cols:
        values["position"] = clean_position

    # rating / habilidade: grava todas as variantes presentes
    if "skill_rating" in cols:
        values["skill_rating"] = skill_value
    if "rating" in cols:
        values["rating"] = skill_value
    if "skill" in cols:
        values["skill"] = skill_value

    # presença/status: grava todas as variantes presentes
    if "status" in cols:
        values["status"] = status_value
    if "presence" in cols:
        values["presence"] = legacy_presence
    if "list" in cols:
        values["list"] = legacy_presence
    if "list_type" in cols:
        values["list_type"] = legacy_presence

    # flags operacionais
    if "arrived" in cols:
        values["arrived"] = False
    if "paid" in cols:
        values["paid"] = False
    if "no_show" in cols:
        values["no_show"] = False
    if "no_show_justified" in cols:
        values["no_show_justified"] = False
    if "no_show_reason" in cols:
        values["no_show_reason"] = None

    # timestamps
    if "created_at" in cols:
        values["created_at"] = now
    if "updated_at" in cols:
        values["updated_at"] = now

    guest_id = None
    try:
        result = db.execute(table.insert().values(**values))
        pk = getattr(result, "inserted_primary_key", None) or []
        if pk:
            guest_id = int(pk[0])
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Falha ao adicionar convidado: {exc}")

    # tenta obter a linha recém-criada do modo mais compatível possível
    try:
        if guest_id is not None and "id" in cols:
            row = db.execute(select(table).where(table.c.id == int(guest_id))).first()
            if row is not None:
                return _guest_row_to_out(row, fallback_match_id=int(match.id))

        if "id" in cols:
            stmt = select(table).where(table.c.match_id == int(match.id))
            if "name" in cols:
                stmt = stmt.where(table.c.name == clean_name)
            if "created_by_user_id" in cols:
                stmt = stmt.where(table.c.created_by_user_id == int(current_user_id))
            elif "user_id" in cols:
                stmt = stmt.where(table.c.user_id == int(current_user_id))
            elif "owner_id" in cols:
                stmt = stmt.where(table.c.owner_id == int(current_user_id))
            if "created_at" in cols:
                stmt = stmt.order_by(table.c.created_at.desc(), table.c.id.desc())
            else:
                stmt = stmt.order_by(table.c.id.desc())
            row = db.execute(stmt.limit(1)).first()
            if row is not None:
                return _guest_row_to_out(row, fallback_match_id=int(match.id))
    except Exception:
        pass

    return {
        "id": int(guest_id or 0),
        "match_id": int(match.id),
        "name": clean_name,
        "position": clean_position,
        "skill_rating": skill_value,
        "status": status_value,
        "arrived": False,
        "no_show": False,
        "no_show_justified": False,
        "no_show_reason": None,
        "created_at": now,
        "updated_at": now,
    }


def _is_monthly_adimplente(db: Session, group_id: str, user_id: int, ref_dt: Optional[datetime] = None) -> bool:
    """Mensalista adimplente.

    Regra prática (robusta):
    - se existir mensalidade do período (ano/mês da partida) com paid=False => inadimplente
    - ou se existir mensalidade vencida (due_date <= hoje) com paid=False => inadimplente
    - caso contrário => adimplente
    """
    today = utc_now().date()
    ref = ref_dt or utc_now()
    y, m = ref.year, ref.month

    period_unpaid = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .filter(GroupFinancialEntry.user_id == user_id)
        .filter(GroupFinancialEntry.entry_type == "monthly")
        .filter(GroupFinancialEntry.period_year == y)
        .filter(GroupFinancialEntry.period_month == m)
        .filter(GroupFinancialEntry.paid.is_(False))
        .first()
    )
    if period_unpaid is not None:
        return False

    overdue = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == group_id)
        .filter(GroupFinancialEntry.user_id == user_id)
        .filter(GroupFinancialEntry.entry_type == "monthly")
        .filter(GroupFinancialEntry.paid.is_(False))
        .filter(GroupFinancialEntry.due_date.isnot(None))
        .filter(GroupFinancialEntry.due_date <= today)
        .first()
    )
    return overdue is None


def _next_queue_position(db: Session, match_id: int, tier: int) -> int:
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


def _confirmed_count(db: Session, match_id: int) -> int:
    return (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .filter(MatchParticipant.status == ParticipantStatus.confirmed.value)
        .count()
    )


def _capacity_ok(match: Match, confirmed_count: int) -> bool:
    return (match.player_limit or 0) <= 0 or confirmed_count < (match.player_limit or 0)


def _match_start_now_reference(match: Match) -> datetime:
    starts_at = getattr(match, "starts_at", None) or getattr(match, "date_time", None)
    if starts_at is None:
        return utc_now()
    tz = getattr(starts_at, "tzinfo", None)
    return datetime.now(tz) if tz is not None else utc_now()


def _total_confirmed_count_compat(db: Session, match_id: int) -> int:
    parts_count = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .filter(MatchParticipant.status == ParticipantStatus.confirmed.value)
        .count()
    )

    guest_table = _guest_table(db)
    guest_cols = _guest_table_columns(db)
    if "status" in guest_cols:
        guest_status_col = guest_table.c.status
        guest_count = db.execute(
            select(guest_table.c.id)
            .where(guest_table.c.match_id == match_id)
            .where(guest_status_col == ParticipantStatus.confirmed.value)
        ).fetchall()
        return int(parts_count) + len(guest_count)

    return int(parts_count)


def _promote_next_waiting_compat(db: Session, match: Match) -> None:
    if not _capacity_ok(match, _total_confirmed_count_compat(db, match.id)):
        return

    candidate = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match.id)
        .filter(MatchParticipant.status == ParticipantStatus.waitlist.value)
        .filter(MatchParticipant.requires_approval.is_(False))
        .order_by(MatchParticipant.waitlist_tier.asc(), MatchParticipant.queue_position.asc().nullslast(), MatchParticipant.id.asc())
        .first()
    )
    if candidate is not None:
        candidate.status = ParticipantStatus.confirmed.value
        if hasattr(candidate, "queue_position"):
            candidate.queue_position = None
        db.add(candidate)
        db.commit()
        return

    guest_table = _guest_table(db)
    guest_cols = _guest_table_columns(db)
    if "status" not in guest_cols:
        return

    waiting_guest = db.execute(
        select(guest_table.c.id)
        .where(guest_table.c.match_id == match.id)
        .where(guest_table.c.status == ParticipantStatus.waitlist.value)
        .order_by(guest_table.c.id.asc())
        .limit(1)
    ).first()
    if waiting_guest is None:
        return

    values = {"status": ParticipantStatus.confirmed.value}
    if "updated_at" in guest_cols:
        values["updated_at"] = utc_now()

    db.execute(guest_table.update().where(guest_table.c.id == int(waiting_guest.id)).values(**values))
    db.commit()


def _remove_member_presence_compat(db: Session, match_id: int, player_id: int) -> bool:
    table = _participant_table(db)
    deleted = db.execute(
        table.delete()
        .where(table.c.match_id == int(match_id))
        .where(table.c.player_id == int(player_id))
    )
    db.commit()
    return bool(getattr(deleted, "rowcount", 0))


def _remove_guest_presence_compat(db: Session, match_id: int, guest_id: int) -> bool:
    table = _guest_table(db)
    deleted = db.execute(
        table.delete()
        .where(table.c.match_id == int(match_id))
        .where(table.c.id == int(guest_id))
    )
    db.commit()
    return bool(getattr(deleted, "rowcount", 0))


def _auto_release_waitlist(db: Session, match: Match, group: Optional[Group] = None) -> bool:
    """Move waitlist -> confirmed automaticamente quando dentro da janela.

    Regras:
    - Só faz sentido para grupos híbridos.
    - Respeita single_waitlist_release_days.
    - Nunca promove quem requires_approval=True (inadimplentes).
    """
    if not match.group_id:
        return False

    grp = group or db.query(Group).filter(Group.id == match.group_id).first()
    if not grp:
        return False

    group_type = str(getattr(grp, "group_type", "") or "").strip().lower()
    if group_type not in {"hibrido", "híbrido", "hybrid"}:
        return False

    days = int(getattr(match, "single_waitlist_release_days", 0) or 0)
    if days <= 0:
        days = int(getattr(grp, "single_waitlist_release_days", 0) or 0)
    if days <= 0:
        return False

    starts_at = getattr(match, "starts_at", None)
    if starts_at is None:
        return False

    # Compatibilidade entre datetime naive/aware.
    if getattr(starts_at, "tzinfo", None) is not None:
        now = datetime.now(starts_at.tzinfo)
    else:
        now = utc_now()

    # Janela aberta quando faltar <= days.
    delta_days = (starts_at - now).total_seconds() / 86400.0
    if delta_days > days:
        return False

    confirmed = _confirmed_count(db, match.id)
    if not _capacity_ok(match, confirmed):
        return False

    changed = False
    waiters = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match.id)
        .filter(MatchParticipant.status == ParticipantStatus.waitlist.value)
        .filter(MatchParticipant.requires_approval.is_(False))
        .order_by(
            MatchParticipant.waitlist_tier.asc(),
            MatchParticipant.queue_position.asc().nullslast(),
            MatchParticipant.id.asc(),
        )
        .all()
    )
    for p in waiters:
        confirmed = _confirmed_count(db, match.id)
        if not _capacity_ok(match, confirmed):
            break
        p.status = ParticipantStatus.confirmed.value
        db.add(p)
        changed = True

    if changed:
        db.commit()
    return changed

def _cents(amount: float) -> int:
    try:
        return int(round((amount or 0) * 100))
    except Exception:
        return 0


def _normalize_match_position(value: str | None) -> str | None:
    raw = (value or '').strip().lower()
    if raw in {'gol', 'goleiro', 'goal', 'goalkeeper', 'keeper'}:
        return 'goalkeeper'
    if raw in {'linha', 'line', 'player'}:
        return 'line'
    return None


def _position_label(value: str | None) -> str | None:
    normalized = _normalize_match_position(value)
    if normalized == 'goalkeeper':
        return 'Gol'
    if normalized == 'line':
        return 'Linha'
    return None


def _resolve_match_slot_defaults(player_limit: int, line_slots: int | None, goalkeeper_slots: int | None) -> tuple[int, int, int]:
    line = int(line_slots or 0)
    goalkeeper = int(goalkeeper_slots or 0)
    total = line + goalkeeper
    if total <= 0:
        total = int(player_limit or 0)
        line = total
        goalkeeper = 0
    return line, goalkeeper, total


# -----------------------------
# Schemas
# -----------------------------
def _ensure_match_venue_financial_entry(db: Session, match: Match, group: Optional[Group] = None) -> None:
    """Garante despesa automática de quadra para partidas de grupo.

    Idempotente por (group_id, entry_type=venue, match_id).
    Atualiza valor/data se o lançamento já existir.
    """
    if not match.group_id:
        return
    group = group or db.query(Group).filter(Group.id == match.group_id).first()
    if not group:
        return
    venue = float(getattr(group, 'venue_cost', 0) or 0)
    if venue <= 0:
        return
    venue_cents = abs(_cents(venue))
    due = match.starts_at.date() if getattr(match, 'starts_at', None) else None

    entry = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == match.group_id)
        .filter(GroupFinancialEntry.user_id.is_(None))
        .filter(GroupFinancialEntry.entry_type == 'venue')
        .filter(GroupFinancialEntry.match_id == match.id)
        .first()
    )
    if entry:
        changed = False
        if int(entry.amount_cents or 0) != int(venue_cents):
            entry.amount_cents = venue_cents
            changed = True
        if entry.due_date != due:
            entry.due_date = due
            changed = True
        wanted_desc = f'Quadra - partida #{match.id}'
        if (entry.description or '') != wanted_desc:
            entry.description = wanted_desc
            changed = True
        if changed:
            db.add(entry)
        return

    db.add(GroupFinancialEntry(
        group_id=match.group_id,
        user_id=None,
        match_id=match.id,
        entry_type='venue',
        amount_cents=venue_cents,
        currency=(group.currency or 'BRL'),
        status='pending',
        due_date=due,
        description=f'Quadra - partida #{match.id}',
        paid=False,
        paid_at=None,
        confirmed_by_user_id=None,
    ))


class MatchCreateIn(BaseModel):
    starts_at: datetime
    ends_at: Optional[datetime] = None
    group_id: Optional[str] = None

    title: Optional[str] = None
    city: Optional[str] = None
    location_name: Optional[str] = Field(default=None, alias="locationName")
    location_lat: Optional[float] = Field(default=None, alias="locationLat")
    location_lng: Optional[float] = Field(default=None, alias="locationLng")
    notes: Optional[str] = None

    is_public: bool = Field(default=False, alias="isPublic")
    player_limit: int = Field(default=0, alias="playerLimit")
    line_slots: int = Field(default=0, alias="lineSlots")
    goalkeeper_slots: int = Field(default=0, alias="goalkeeperSlots")
    modality: Optional[str] = None
    gender_type: Optional[str] = Field(default=None, alias="genderType")

    # Grupo híbrido: dias antes da partida em que avulsos podem sair da waitlist automaticamente (0 = manual)
    single_waitlist_release_days: int = Field(default=0, alias="singleWaitlistReleaseDays")

    price_cents: Optional[int] = Field(default=None, alias="priceCents")
    currency: Optional[str] = None
    payment_method: Optional[str] = Field(default=None, alias="paymentMethod")
    payment_key: Optional[str] = Field(default=None, alias="paymentKey")


class MatchUpdateIn(BaseModel):
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    title: Optional[str] = None
    single_waitlist_release_days: Optional[int] = Field(default=None, alias="singleWaitlistReleaseDays")
    city: Optional[str] = None
    location_name: Optional[str] = Field(default=None, alias="locationName")
    location_lat: Optional[float] = Field(default=None, alias="locationLat")
    location_lng: Optional[float] = Field(default=None, alias="locationLng")
    notes: Optional[str] = None
    is_public: Optional[bool] = Field(default=None, alias="isPublic")
    player_limit: Optional[int] = Field(default=None, alias="playerLimit")
    line_slots: Optional[int] = Field(default=None, alias="lineSlots")
    goalkeeper_slots: Optional[int] = Field(default=None, alias="goalkeeperSlots")
    modality: Optional[str] = None
    gender_type: Optional[str] = Field(default=None, alias="genderType")
    price_cents: Optional[int] = Field(default=None, alias="priceCents")
    currency: Optional[str] = None
    payment_method: Optional[str] = Field(default=None, alias="paymentMethod")
    payment_key: Optional[str] = Field(default=None, alias="paymentKey")


class MatchOut(BaseModel):
    id: int
    owner_id: int
    group_id: Optional[str] = None
    starts_at: datetime
    ends_at: Optional[datetime] = None
    title: Optional[str] = None
    status: str
    player_limit: int
    line_slots: int = 0
    goalkeeper_slots: int = 0
    modality: Optional[str] = None
    gender_type: Optional[str] = None
    is_public: bool
    single_waitlist_release_days: int = 0
    city: Optional[str] = None
    location_name: Optional[str] = None
    location_lat: Optional[float] = None
    location_lng: Optional[float] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class ParticipantOut(BaseModel):
    id: int
    match_id: int
    player_id: int
    status: str
    arrived: bool
    paid: bool
    position: Optional[str] = None

    class Config:
        from_attributes = True


class ParticipantUpdateIn(BaseModel):
    status: str = Field(..., description="confirmed | waitlist | rejected")
    arrived: Optional[bool] = None
    paid: Optional[bool] = None


class DrawIn(BaseModel):
    teams: int = Field(default=2, ge=2, le=2, description="por enquanto apenas 2 times")


class DrawPlayerOut(BaseModel):
    id: int
    name: str
    kind: str = "member"
    skill: Optional[int] = None
    player_id: Optional[int] = None
    guest_id: Optional[int] = None
    position: Optional[str] = None


class DrawTeamOut(BaseModel):
    team_number: int
    label: str
    players: List[DrawPlayerOut]
    total_skill: Optional[int] = None


class DrawMetaOut(BaseModel):
    can_view_skill: bool
    skill_visibility: str
    eligible_players: int = 0
    players_per_team: Optional[int] = None
    source: str = "saved"


class DrawResponseOut(BaseModel):
    ok: bool = True
    teams: List[DrawTeamOut]
    meta: DrawMetaOut


# -----------------------------
# Helpers
# -----------------------------

def _can_view_draw_skill(db: Session, match: Match, user_id: int) -> bool:
    if match.group_id:
        try:
            _, gm = get_group_member(db, match.group_id, user_id)
            return (getattr(gm, "role", "") or "").strip().lower() in ("owner", "admin")
        except HTTPException:
            return False
    return int(getattr(match, "owner_id", 0) or 0) == int(user_id)


def _serialize_draw_team_rows(
    db: Session,
    match: Match,
    rows: list[MatchDrawTeam],
    *,
    current_user_id: int,
    players_per_team: int | None = None,
    eligible_players: int | None = None,
    source: str = "saved",
) -> dict:
    include_skill = _can_view_draw_skill(db, match, current_user_id)
    response_teams = []
    ordered_rows = sorted(rows, key=lambda row: int(getattr(row, "team_number", 0) or 0))
    for row in ordered_rows:
        raw_players = list(getattr(row, "players", None) or [])
        players_out = []
        total_skill = 0
        for item in raw_players:
            if not isinstance(item, dict):
                continue
            player = dict(item)
            skill_value = max(1, min(5, int(player.get("skill", 3) or 3)))
            total_skill += skill_value
            player_out = {
                "id": int(player.get("id") or player.get("player_id") or player.get("guest_id") or 0),
                "name": (player.get("name") or "Jogador").strip() or "Jogador",
                "kind": (player.get("kind") or "member").strip() or "member",
                "player_id": player.get("player_id"),
                "guest_id": player.get("guest_id"),
                "position": _position_label(player.get("position")) or player.get("position"),
            }
            if include_skill:
                player_out["skill"] = skill_value
            players_out.append(player_out)

        response_team = {
            "team_number": int(getattr(row, "team_number", 0) or 0),
            "label": f"Time {int(getattr(row, 'team_number', 0) or 0)}",
            "players": players_out,
        }
        if include_skill:
            response_team["total_skill"] = total_skill
            response_team["skill_total"] = total_skill
        response_teams.append(response_team)

    inferred_eligible = sum(len(team["players"]) for team in response_teams)
    resolved_eligible_players = int(eligible_players if eligible_players is not None else inferred_eligible)
    resolved_skill_visibility = "owner_admin" if include_skill else "hidden_for_member"
    meta = {
        "can_view_skill": include_skill,
        "skill_visibility": resolved_skill_visibility,
        "eligible_players": resolved_eligible_players,
        "players_per_team": players_per_team,
        "source": source,
    }
    return {
        "ok": True,
        "teams": response_teams,
        "meta": meta,
        # Campos redundantes para compatibilidade com o frontend atual.
        "can_view_skill": include_skill,
        "can_view_metrics": include_skill,
        "skill_visibility": resolved_skill_visibility,
        "eligible_count": resolved_eligible_players,
        "players_per_team": players_per_team,
        "source": source,
    }


def _get_saved_draw_response(db: Session, match: Match, *, current_user_id: int) -> dict:
    rows = (
        db.query(MatchDrawTeam)
        .filter(MatchDrawTeam.match_id == match.id)
        .order_by(MatchDrawTeam.team_number.asc(), MatchDrawTeam.id.asc())
        .all()
    )
    inferred_players_per_team = max((len(list(getattr(row, "players", None) or [])) for row in rows), default=0) or None
    return _serialize_draw_team_rows(
        db,
        match,
        rows,
        current_user_id=current_user_id,
        players_per_team=inferred_players_per_team,
        eligible_players=sum(len(list(getattr(row, "players", None) or [])) for row in rows),
        source="saved",
    )


def _saved_draw_rows(db: Session, match_id: int) -> list[MatchDrawTeam]:
    return (
        db.query(MatchDrawTeam)
        .filter(MatchDrawTeam.match_id == match_id)
        .order_by(MatchDrawTeam.team_number.asc(), MatchDrawTeam.id.asc())
        .all()
    )


def _acquire_match_draw_lock(db: Session, match_id: int) -> None:
    bind = getattr(db, "bind", None)
    dialect = getattr(getattr(bind, "dialect", None), "name", "") or ""
    if dialect.startswith("postgres"):
        try:
            acquired = db.execute(text("SELECT pg_try_advisory_xact_lock(:lock_key)"), {"lock_key": int(match_id)}).scalar()
        except Exception:
            acquired = False
        if not acquired:
            raise api_error(409, code="DRAW_IN_PROGRESS", message="Já existe um sorteio em processamento para esta partida.")
        return

    try:
        db.query(Match).filter(Match.id == match_id).with_for_update(nowait=True).one()
    except Exception:
        raise api_error(409, code="DRAW_IN_PROGRESS", message="Já existe um sorteio em processamento para esta partida.")


def _draw_pool_signature(items: list[dict]) -> list[str]:
    normalized: list[str] = []
    for item in items:
        kind = (item.get("kind") or "member").strip().lower()
        if kind == "guest":
            raw_id = item.get("guest_id") or item.get("id") or 0
            normalized.append(f"guest:{int(raw_id)}")
        else:
            raw_id = item.get("player_id") or item.get("id") or 0
            normalized.append(f"member:{int(raw_id)}")
    normalized.sort()
    return normalized


def _deduplicate_draw_pool(pool: list[dict]) -> tuple[list[dict], list[str]]:
    deduped: list[dict] = []
    seen: set[str] = set()
    removed: list[str] = []
    for item in pool:
        signature = ""
        kind = (item.get("kind") or "member").strip().lower()
        if kind == "guest":
            raw_id = item.get("guest_id") or item.get("id") or 0
            signature = f"guest:{int(raw_id)}"
        else:
            raw_id = item.get("player_id") or item.get("id") or 0
            signature = f"member:{int(raw_id)}"
        if signature in seen:
            removed.append(signature)
            continue
        seen.add(signature)
        deduped.append(item)
    return deduped, removed


def _saved_draw_matches_current_state(saved_rows: list[MatchDrawTeam], pool: list[dict], players_per_team: int) -> bool:
    if not saved_rows:
        return False

    expected_team_count = max(2, int(__import__("math").ceil(len(pool) / players_per_team)))
    if len(saved_rows) != expected_team_count:
        return False

    saved_pool: list[dict] = []
    max_saved_team_size = 0
    total_saved_players = 0
    for row in saved_rows:
        raw_players = list(getattr(row, "players", None) or [])
        if len(raw_players) > int(players_per_team):
            return False
        total_saved_players += len(raw_players)
        max_saved_team_size = max(max_saved_team_size, len(raw_players))
        for player in raw_players:
            if isinstance(player, dict):
                saved_pool.append(player)

    if max_saved_team_size != int(players_per_team):
        return False
    if total_saved_players != len(pool):
        return False

    return _draw_pool_signature(saved_pool) == _draw_pool_signature(pool)


def _require_match(db: Session, match_id: int) -> Match:
    m = db.query(Match).filter(Match.id == match_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Match not found")
    return m


def _require_can_manage_match(db: Session, match: Match, user_id: int):
    """Permissões:
    - se match tiver group_id: admin/owner do grupo podem gerenciar
    - caso contrário: apenas owner_id do match
    """
    if match.group_id:
        require_group_admin(db, match.group_id, user_id)
        return
    if match.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Sem permissão")


# -----------------------------
# Endpoints
# -----------------------------
@router.get("", response_model=List[MatchOut])
def list_matches(
    group_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    q = db.query(Match)
    if group_id is not None:
        # ver se usuário é membro do grupo
        get_group_member(db, group_id, current_user_id)
        q = q.filter(Match.group_id == group_id)
    else:
        q = q.filter(Match.owner_id == current_user_id)

    return q.order_by(Match.starts_at.desc()).all()


# =========================================================
# GROUP-SCOPED (hierarquia: /groups/{group_id}/matches)
# =========================================================


@group_router.get("", response_model=List[MatchOut])
def list_group_matches(
    group_id: str,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    # membro do grupo pode listar
    get_group_member(db, group_id, current_user_id)
    return (
        db.query(Match)
        .filter(Match.group_id == group_id)
        .order_by(Match.starts_at.desc())
        .all()
    )


@group_router.post("", response_model=MatchOut, status_code=201)
def create_group_match(
    group_id: str,
    payload: MatchCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    # apenas admin/owner do grupo podem criar
    require_group_admin(db, group_id, current_user_id)

    data = payload.model_dump(by_alias=False)
    data["group_id"] = group_id

    # Compatibilidade com schema legado do banco: essas colunas ainda são NOT NULL em produção
    starts_at = data["starts_at"]
    price_cents = data.get("price_cents")
    value_per_player = None
    if price_cents is not None:
        try:
            value_per_player = float(int(price_cents) / 100.0)
        except Exception:
            value_per_player = None

    ends_at = data.get("ends_at") or (starts_at + timedelta(hours=2))
    if ends_at <= starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")

    line_slots, goalkeeper_slots, total_slots = _resolve_match_slot_defaults(
        int(data.get("player_limit", 0) or 0),
        data.get("line_slots"),
        data.get("goalkeeper_slots"),
    )

    grp = db.query(Group).filter(Group.id == group_id).first()
    release_days = int(data.get("single_waitlist_release_days", 0) or 0)
    if release_days <= 0 and grp is not None and (getattr(grp, "group_type", "") or "").lower() == "hibrido":
        release_days = int(getattr(grp, "single_waitlist_release_days", 0) or 0)

    m = Match(
        owner_id=current_user_id,
        group_id=group_id,
        starts_at=starts_at,
        ends_at=ends_at,
        date_time=starts_at,
        title=data.get("title"),
        city=data.get("city"),
        location_name=data.get("location_name"),
        venue_name=data.get("location_name") or data.get("title") or "Partida",
        notes=data.get("notes"),
        is_public=bool(data.get("is_public", False)),
        player_limit=total_slots,
        line_slots=line_slots,
        goalkeeper_slots=goalkeeper_slots,
        modality=(data.get("modality") or None),
        gender_type=(data.get("gender_type") or None),
        single_waitlist_release_days=release_days,
        price_cents=price_cents,
        value_per_player=value_per_player if value_per_player is not None else 0.0,
        currency=data.get("currency"),
        payment_method=data.get("payment_method"),
        payment_key=data.get("payment_key"),
        status=(data.get("status") or "scheduled"),
    )
    try:
        db.add(m)
        db.commit()
        db.refresh(m)
        _ensure_match_venue_financial_entry(db, m, grp)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar partida: {e}")

    # criador entra como participante confirmado
    player = get_user_primary_player(db, current_user_id)
    try:
        p = MatchParticipant(match_id=m.id, user_id=current_user_id, player_id=player.id, status=ParticipantStatus.confirmed.value)
        db.add(p)
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        db.rollback()

    db.refresh(m)
    try:
        dispatch_match_created(db, m, current_user_id)
        db.commit()
    except Exception:
        db.rollback()
    return m


@group_router.get("/{match_id}", response_model=MatchOut)
def get_group_match(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    m = _require_match(db, match_id)
    if m.group_id != group_id:
        raise HTTPException(status_code=404, detail="Match not found")
    return m


@group_router.put("/{match_id}", response_model=MatchOut)
def update_group_match(
    group_id: str,
    match_id: int,
    payload: MatchUpdateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    require_group_admin(db, group_id, current_user_id)
    m = _require_match(db, match_id)
    if m.group_id != group_id:
        raise HTTPException(status_code=404, detail="Match not found")

    data = payload.model_dump(exclude_unset=True, by_alias=False)
    starts_at_new = data.get("starts_at", m.starts_at)
    ends_at_new = data.get("ends_at", m.ends_at or (starts_at_new + timedelta(hours=2)))
    if ends_at_new <= starts_at_new:
        raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")
    if "starts_at" in data and "ends_at" not in data and m.ends_at:
        duration = m.ends_at - m.starts_at
        data["ends_at"] = data["starts_at"] + duration
    for k, v in data.items():
        setattr(m, k, v)
    db.add(m)
    db.commit()
    db.refresh(m)
    _ensure_match_venue_financial_entry(db, m)
    db.commit()
    db.refresh(m)
    return m


@group_router.delete("/{match_id}")
def delete_group_match(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    require_group_admin(db, group_id, current_user_id)
    m = _require_match(db, match_id)
    if m.group_id != group_id:
        raise HTTPException(status_code=404, detail="Match not found")

    try:
        db.execute(text("DELETE FROM payments WHERE match_id = :match_id"), {"match_id": match_id})
        db.delete(m)
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir partida: {e}")

    return {"ok": True}


@router.post("", response_model=MatchOut, status_code=201)
def create_match(
    payload: MatchCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    # se for match de grupo, apenas admin/owner podem criar
    if payload.group_id is not None:
        require_group_admin(db, payload.group_id, current_user_id)

    ends_at = payload.ends_at or (payload.starts_at + timedelta(hours=2))
    if ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")

    m = Match(
        owner_id=current_user_id,
        group_id=payload.group_id,
        starts_at=payload.starts_at,
        ends_at=ends_at,
        date_time=payload.starts_at,
        title=payload.title,
        city=payload.city,
        location_name=payload.location_name,
        venue_name=payload.location_name or payload.title or "Partida",
        notes=payload.notes,
        is_public=payload.is_public,
        player_limit=payload.player_limit,
        line_slots=payload.line_slots or payload.player_limit,
        goalkeeper_slots=payload.goalkeeper_slots or 0,
        modality=(payload.modality or None),
        gender_type=(payload.gender_type or None),
        single_waitlist_release_days=payload.single_waitlist_release_days,
        price_cents=payload.price_cents,
        currency=payload.currency,
        payment_method=payload.payment_method,
        payment_key=payload.payment_key,
        status=(payload.title and "scheduled") or "scheduled",
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    if m.group_id:
        _ensure_match_venue_financial_entry(db, m)
        db.commit()
        db.refresh(m)

    # regra prática: criador entra automaticamente como participante confirmado
    player = get_user_primary_player(db, current_user_id)
    try:
        p = MatchParticipant(match_id=m.id, user_id=current_user_id, player_id=player.id, status=ParticipantStatus.confirmed.value)
        db.add(p)
        db.commit()
    except IntegrityError:
        db.rollback()

    db.refresh(m)
    try:
        if m.group_id:
            dispatch_match_created(db, m, current_user_id)
            db.commit()
    except Exception:
        db.rollback()
    return m


@router.get("/{match_id}", response_model=MatchOut)
def get_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if m.group_id:
        get_group_member(db, m.group_id, current_user_id)
    elif m.owner_id != current_user_id and not m.is_public:
        raise HTTPException(status_code=403, detail="Sem permissão")
    return m


@router.put("/{match_id}", response_model=MatchOut)
def update_match(
    match_id: int,
    payload: MatchUpdateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)

    data = payload.model_dump(exclude_unset=True, by_alias=False)
    starts_at_new = data.get("starts_at", m.starts_at)
    ends_at_new = data.get("ends_at", m.ends_at or (starts_at_new + timedelta(hours=2)))
    if ends_at_new <= starts_at_new:
        raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")
    if "starts_at" in data and "ends_at" not in data and m.ends_at:
        duration = m.ends_at - m.starts_at
        data["ends_at"] = data["starts_at"] + duration
    for k, v in data.items():
        setattr(m, k, v)
    db.add(m)
    db.commit()
    db.refresh(m)
    if m.group_id:
        _ensure_match_venue_financial_entry(db, m)
        db.commit()
        db.refresh(m)
    return m


@router.delete("/{match_id}")
def delete_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    db.delete(m)
    db.commit()
    return {"ok": True}


# ------------------------------------------------
# PRESENÇA (confirmed vs waitlist) + guests
# ------------------------------------------------

class PresenceItemOut(BaseModel):
    kind: str  # member|guest
    id: int    # player_id or guest_id
    name: str
    presence: str  # confirmed|waiting
    arrived: bool = False
    paid: bool = False
    no_show: bool = False
    no_show_justified: bool = False
    no_show_reason: Optional[str] = None
    billing_type: Optional[str] = None
    requires_approval: bool = False
    queue_position: Optional[int] = None
    position: Optional[str] = None


class PresenceOut(BaseModel):
    match_id: int
    confirmed: List[PresenceItemOut]
    waiting: List[PresenceItemOut]


@router.get("/{match_id}/presence", response_model=PresenceOut)
def get_presence(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    return build_presence(db, match=m, current_user_id=current_user_id)


@router.post("/{match_id}/presence", response_model=PresenceOut)
class ConfirmPresenceIn(BaseModel):
    position: str


def confirm_presence(
    match_id: int,
    payload: ConfirmPresenceIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    result = confirm_presence_for_user(db, match=m, current_user_id=current_user_id, position=payload.position)
    log_event(logger, "match_presence_confirmed", user_id=current_user_id, group_id=m.group_id, match_id=match_id, position=payload.position)
    return result


class RemovePresenceIn(BaseModel):
    player_id: Optional[int] = None
    target: Optional[str] = None
    target_id: Optional[int] = None


class AdminMarkIn(BaseModel):
    target: str = Field(..., description="member|guest")
    target_id: int = Field(..., alias="target_id")
    arrived: Optional[bool] = None
    paid: Optional[bool] = None


@router.delete("/{match_id}/presence", response_model=PresenceOut)
def remove_presence(
    match_id: int,
    payload: RemovePresenceIn = Body(default_factory=RemovePresenceIn),
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    result = remove_presence_entry(
        db,
        match=m,
        current_user_id=current_user_id,
        player_id=payload.player_id,
        target=payload.target,
        target_id=payload.target_id,
    )
    log_event(logger, "match_presence_removed", user_id=current_user_id, group_id=m.group_id, match_id=match_id, target=payload.target, target_id=payload.target_id or payload.player_id)
    return result


class ApproveMemberIn(BaseModel):
    player_id: int
    position: Optional[str] = None


@router.post("/{match_id}/approve-member", response_model=PresenceOut)
def approve_member_presence(
    match_id: int,
    payload: ApproveMemberIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    result = approve_member_presence_entry(
        db,
        match=m,
        player_id=payload.player_id,
        current_user_id=current_user_id,
        position=payload.position,
    )
    log_event(logger, "match_waiting_member_approved", user_id=current_user_id, group_id=m.group_id, match_id=match_id, player_id=payload.player_id, position=payload.position)
    return result


@router.post("/{match_id}/admin/mark", response_model=PresenceOut)
def admin_mark(
    match_id: int,
    payload: AdminMarkIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    result = admin_mark_presence(
        db,
        match=m,
        current_user_id=current_user_id,
        target=payload.target,
        target_id=payload.target_id,
        arrived=payload.arrived,
        paid=payload.paid,
    )
    log_event(logger, "match_admin_mark", user_id=current_user_id, group_id=m.group_id, match_id=match_id, target=payload.target, target_id=payload.target_id, arrived=payload.arrived, paid=payload.paid)
    return result

@router.get("/{match_id}/participants", response_model=List[ParticipantOut])
def list_participants(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if m.group_id:
        get_group_member(db, m.group_id, current_user_id)
    elif m.owner_id != current_user_id and not m.is_public:
        raise HTTPException(status_code=403, detail="Sem permissão")

    return (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .order_by(MatchParticipant.id.asc())
        .all()
    )


# ------------------------------------------------
# MATCH JOIN REQUESTS (jogadores fora do grupo)
# ------------------------------------------------

class MatchJoinRequestOut(BaseModel):
    id: int
    match_id: int
    requester_user_id: int
    player_id: int
    status: str
    created_at: datetime
    updated_at: datetime
    requester_name: Optional[str] = None


class MatchJoinRequestCreateIn(BaseModel):
    message: Optional[str] = None


@router.post("/{match_id}/join-requests", response_model=MatchJoinRequestOut, status_code=201)
def create_match_join_request(
    match_id: int,
    payload: MatchJoinRequestCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Compat route (prefira /groups/{group_id}/matches/{match_id}/join-requests).

    Permite que jogador não-membro solicite vaga em partida pública.
    """
    m = _require_match(db, match_id)
    if not bool(m.is_public):
        raise HTTPException(status_code=403, detail="Esta partida não está aberta para solicitações")
    if (m.status or "").lower() != "scheduled":
        raise HTTPException(status_code=400, detail="Partida não está disponível para solicitações")

    # Se a partida pertence a um grupo e o usuário já é membro, deve usar presença normal
    if m.group_id:
        try:
            get_group_member(db, m.group_id, current_user_id)
            raise HTTPException(status_code=400, detail="Você já é membro do grupo. Use a confirmação de presença.")
        except HTTPException as e:
            if e.status_code != 403:
                raise

    player = get_user_primary_player(db, current_user_id)

    existing = (
        db.query(MatchJoinRequest)
        .filter(MatchJoinRequest.match_id == match_id)
        .filter(MatchJoinRequest.player_id == player.id)
        .first()
    )
    if existing:
        if (existing.status or "").lower() == JoinStatus.rejected.value:
            existing.status = JoinStatus.pending.value
        if hasattr(existing, "message"):
            existing.message = payload.message
        if hasattr(existing, "group_id") and m.group_id:
            existing.group_id = m.group_id
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    req = MatchJoinRequest(
        match_id=match_id,
        user_id=current_user_id,
        player_id=player.id,
        status=JoinStatus.pending.value,
        group_id=m.group_id,
        message=payload.message,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return req

@router.get("/{match_id}/join-requests", response_model=List[MatchJoinRequestOut])
def list_match_join_requests(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    return (
        db.query(MatchJoinRequest)
        .filter(MatchJoinRequest.match_id == match_id)
        .filter(MatchJoinRequest.status == "pending")
        .order_by(MatchJoinRequest.created_at.asc())
        .all()
    )


@router.post("/{match_id}/join-requests/{request_id}/approve")
def approve_match_join_request(
    match_id: int,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)

    req = (
        db.query(MatchJoinRequest)
        .filter(MatchJoinRequest.match_id == match_id)
        .filter(MatchJoinRequest.id == request_id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    if (req.status or "").lower() != "pending":
        return {"ok": True}

    # aprova => cria participante (segue regras de capacidade; aqui entra como waitlist se lotado)
    confirmed_count = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .filter(MatchParticipant.status == ParticipantStatus.confirmed.value)
        .count()
    )
    capacity_ok = (m.player_limit or 0) <= 0 or confirmed_count < (m.player_limit or 0)
    status = ParticipantStatus.confirmed.value if capacity_ok else ParticipantStatus.waitlist.value

    exists = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match_id)
        .filter(MatchParticipant.player_id == req.player_id)
        .first()
    )
    if not exists:
        db.add(MatchParticipant(match_id=match_id, user_id=req.user_id, player_id=req.player_id, status=status))

    req.status = JoinStatus.active.value
    db.add(req)
    db.commit()
    return {"ok": True}


@router.post("/{match_id}/join-requests/{request_id}/reject")
def reject_match_join_request(
    match_id: int,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)

    req = (
        db.query(MatchJoinRequest)
        .filter(MatchJoinRequest.match_id == match_id)
        .filter(MatchJoinRequest.id == request_id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")

    req.status = JoinStatus.rejected.value
    db.add(req)
    db.commit()
    return {"ok": True}


# ------------------------------------------------
# GUEST PLAYERS (sem app)
# ------------------------------------------------

class GuestCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    position: Optional[str] = None
    skill_rating: int = Field(default=3, ge=1, le=5, validation_alias=AliasChoices("skill_rating", "skillRating"))
    presence: str = Field(default="confirmed", validation_alias=AliasChoices("presence", "status"))


class GuestOut(BaseModel):
    id: int
    match_id: int
    name: str
    position: Optional[str] = None
    skill_rating: int
    status: str
    arrived: bool
    no_show: bool
    no_show_justified: bool
    no_show_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("/{match_id}/guests", response_model=List[GuestOut])
def list_guests(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    return list_guests_for_match(db, match_id=match_id)


# ✅ Hierarquia do grupo (sem quebrar compat): /groups/{group_id}/matches/{match_id}/guests
@group_router.get("/{match_id}/guests", response_model=List[GuestOut])
def list_guests_in_group(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    _require_can_manage_match(db, m, current_user_id)
    return list_guests_for_match(db, match_id=match_id)




# ✅ Presence na hierarquia do grupo
@group_router.get("/{match_id}/presence", response_model=PresenceOut)
def get_presence_in_group(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return get_presence(match_id=match_id, db=db, current_user_id=current_user_id)


@group_router.delete("/{match_id}/presence", response_model=PresenceOut)
def remove_presence_in_group(
    group_id: str,
    match_id: int,
    payload: RemovePresenceIn = Body(default_factory=RemovePresenceIn),
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return remove_presence(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/presence", response_model=PresenceOut)
def confirm_presence_in_group(
    group_id: str,
    match_id: int,
    payload: ConfirmPresenceIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return confirm_presence(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/approve-member", response_model=PresenceOut)
def group_approve_member_presence(
    group_id: str,
    match_id: int,
    payload: ApproveMemberIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    require_group_admin(db, group_id, current_user_id)
    m = _require_match(db, match_id)
    if m.group_id != group_id:
        raise HTTPException(status_code=404, detail="Match not found")
    return approve_member_presence(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/admin/mark", response_model=PresenceOut)
def admin_mark_in_group(
    group_id: str,
    match_id: int,
    payload: AdminMarkIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return admin_mark(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.get("/{match_id}/join-requests", response_model=List[MatchJoinRequestOut])
def list_match_join_requests_in_group(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return list_match_join_requests(match_id=match_id, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/join-requests", response_model=MatchJoinRequestOut, status_code=201)
def create_match_join_request_in_group(
    group_id: str,
    match_id: int,
    payload: MatchJoinRequestCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return create_match_join_request(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/join-requests/{request_id}/approve")
def approve_match_join_request_in_group(
    group_id: str,
    match_id: int,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return approve_match_join_request(match_id=match_id, request_id=request_id, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/join-requests/{request_id}/reject")
def reject_match_join_request_in_group(
    group_id: str,
    match_id: int,
    request_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return reject_match_join_request(match_id=match_id, request_id=request_id, db=db, current_user_id=current_user_id)


@router.post("/{match_id}/guests", response_model=GuestOut, status_code=201)
def add_guest(
    match_id: int,
    payload: GuestCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    result = add_guest_to_match(
        db,
        match=m,
        current_user_id=current_user_id,
        name=payload.name,
        position=payload.position,
        skill_rating=payload.skill_rating,
        presence=payload.presence or payload.status,
    )
    log_event(logger, "match_guest_added", user_id=current_user_id, group_id=m.group_id, match_id=match_id, guest_name=payload.name, position=payload.position, presence=payload.presence or payload.status)
    return result


    # =====================================================
    # FASE 4 - CHECK-IN / NO-SHOW (dentro do grupo)
    # =====================================================

class AttendanceNoShowIn(BaseModel):
    justified: bool = False
    reason: Optional[str] = None


class AttendanceActionOut(BaseModel):
    ok: bool = True
    attendance_id: int
    match_id: int
    fine_created: bool = False
    fine_entry_id: Optional[int] = None


@group_router.post("/{match_id}/attendance/{attendance_id}/check-in", response_model=AttendanceActionOut)
def group_check_in_attendance(
    group_id: str,
    match_id: int,
    attendance_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Owner/ADM marca que o jogador chegou (check-in)."""
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")

    _require_can_manage_match(db, m, current_user_id)

    p = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.id == attendance_id)
        .filter(MatchParticipant.match_id == match_id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Presença não encontrada")

    p.arrived = True
    # se foi marcado como no-show antes, limpa
    if hasattr(p, "no_show"):
        p.no_show = False
    if hasattr(p, "no_show_justified"):
        p.no_show_justified = False
    if hasattr(p, "no_show_reason"):
        p.no_show_reason = None

    db.add(p)
    db.commit()

    return AttendanceActionOut(attendance_id=p.id, match_id=match_id, fine_created=False, fine_entry_id=None)


@group_router.post("/{match_id}/attendance/{attendance_id}/mark-no-show", response_model=AttendanceActionOut)
def group_mark_no_show(
    group_id: str,
    match_id: int,
    attendance_id: int,
    payload: AttendanceNoShowIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Owner/ADM marca falta (no-show). Se multa estiver ativa no grupo, gera lançamento automático."""
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")

    _require_can_manage_match(db, m, current_user_id)

    p = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.id == attendance_id)
        .filter(MatchParticipant.match_id == match_id)
        .first()
    )
    if not p:
        raise HTTPException(status_code=404, detail="Presença não encontrada")

    p.arrived = False
    if hasattr(p, "no_show"):
        p.no_show = True
    if hasattr(p, "no_show_justified"):
        p.no_show_justified = bool(payload.justified)
    if hasattr(p, "no_show_reason"):
        p.no_show_reason = (payload.reason.strip() if payload.reason else None)

    # Gera multa (idempotente via UniqueConstraint em group_financial_entries)
    fine_created = False
    fine_entry_id: Optional[int] = None

    grp: Group = db.query(Group).filter(Group.id == group_id).first()
    if not grp:
        raise HTTPException(status_code=404, detail="Grupo não encontrado")

    # Descobre user_id a partir do player
    player: Player = db.query(Player).filter(Player.id == p.player_id).first()
    fined_user_id = getattr(player, "owner_id", None)

    if (
        fined_user_id is not None
        and bool(getattr(grp, "fine_enabled", False))
        and not bool(payload.justified)
        and (getattr(grp, "fine_amount", None) or 0) > 0
    ):
        existing = (
            db.query(GroupFinancialEntry)
            .filter(GroupFinancialEntry.group_id == group_id)
            .filter(GroupFinancialEntry.user_id == int(fined_user_id))
            .filter(GroupFinancialEntry.match_id == match_id)
            .filter(GroupFinancialEntry.entry_type == "fine")
            .first()
        )
        if existing:
            fine_entry_id = existing.id
        else:
            amount_cents = int(round(float(grp.fine_amount) * 100))
            currency = (getattr(grp, "currency", None) or "EUR")
            desc = (getattr(grp, "fine_reason", None) or "Falta injustificada")

            entry = GroupFinancialEntry(
                group_id=group_id,
                user_id=int(fined_user_id),
                match_id=match_id,
                entry_type="fine",
                amount_cents=amount_cents,
                currency=currency,
                status="pending",
                due_date=utc_now().date(),
                description=desc,
                paid=False,
                no_show=True,
                no_show_justified=False,
                no_show_reason=(payload.reason.strip() if payload.reason else desc),
                confirmed_by_user_id=int(current_user_id),
            )
            db.add(entry)
            try:
                db.commit()
                db.refresh(entry)
                fine_created = True
                fine_entry_id = entry.id
            except IntegrityError:
                db.rollback()
                # outro processo criou primeiro
                existing2 = (
                    db.query(GroupFinancialEntry)
                    .filter(GroupFinancialEntry.group_id == group_id)
                    .filter(GroupFinancialEntry.user_id == int(fined_user_id))
                    .filter(GroupFinancialEntry.match_id == match_id)
                    .filter(GroupFinancialEntry.entry_type == "fine")
                    .first()
                )
                if existing2:
                    fine_entry_id = existing2.id

    db.add(p)
    db.commit()

    return AttendanceActionOut(
        attendance_id=p.id,
        match_id=match_id,
        fine_created=fine_created,
        fine_entry_id=fine_entry_id,
    )


@group_router.post("/{match_id}/guests", response_model=GuestOut, status_code=201)
def add_guest_in_group(
    group_id: str,
    match_id: int,
    payload: GuestCreateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    _require_can_manage_match(db, m, current_user_id)
    return _insert_guest_compat(db, m, payload, current_user_id)


@router.patch("/{match_id}/guests/{guest_id}", response_model=GuestOut)
def update_guest(
    match_id: int,
    guest_id: int,
    arrived: Optional[bool] = None,
    status: Optional[str] = None,
    position: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    return update_guest_for_match(
        db,
        match_id=match_id,
        guest_id=guest_id,
        arrived=arrived,
        status=status,
        position=position,
    )


@group_router.patch("/{match_id}/guests/{guest_id}", response_model=GuestOut)
def update_guest_in_group(
    group_id: str,
    match_id: int,
    guest_id: int,
    arrived: Optional[bool] = None,
    status: Optional[str] = None,
    position: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    _require_can_manage_match(db, m, current_user_id)
    return update_guest_for_match(
        db,
        match_id=match_id,
        guest_id=guest_id,
        arrived=arrived,
        status=status,
        position=position,
    )


@router.delete("/{match_id}/guests/{guest_id}")
def delete_guest(
    match_id: int,
    guest_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    delete_guest_from_match(db, match_id=match_id, guest_id=guest_id)
    return {"ok": True}


@group_router.delete("/{match_id}/guests/{guest_id}")
def delete_guest_in_group(
    group_id: str,
    match_id: int,
    guest_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return delete_guest(match_id=match_id, guest_id=guest_id, db=db, current_user_id=current_user_id)


@router.post("/{match_id}/participants", response_model=ParticipantOut, status_code=201)
def join_match(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    # se for de grupo, precisa ser membro ativo
    if m.group_id:
        get_group_member(db, m.group_id, current_user_id)
    elif not m.is_public and m.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Sem permissão")

    player = get_user_primary_player(db, current_user_id)
    p = MatchParticipant(match_id=match_id, user_id=current_user_id, player_id=player.id, status=ParticipantStatus.confirmed.value)
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Player já está na partida")
    db.refresh(p)
    return p


@router.patch("/{match_id}/participants/{participant_id}", response_model=ParticipantOut)
def update_participant(
    match_id: int,
    participant_id: int,
    payload: ParticipantUpdateIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    part = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.id == participant_id, MatchParticipant.match_id == match_id)
        .first()
    )
    if not part:
        raise HTTPException(status_code=404, detail="Participant not found")

    # self-update: status/arrived/paid apenas do próprio player
    player = get_user_primary_player(db, current_user_id)
    if part.player_id != player.id:
        _require_can_manage_match(db, m, current_user_id)

    if payload.status not in {
        ParticipantStatus.confirmed.value,
        ParticipantStatus.waitlist.value,
        ParticipantStatus.rejected.value,
    }:
        raise HTTPException(status_code=422, detail="status inválido")

    part.status = payload.status
    if payload.arrived is not None:
        part.arrived = bool(payload.arrived)
    if payload.paid is not None:
        part.paid = bool(payload.paid)

    db.add(part)
    db.commit()
    db.refresh(part)
    return part


class DrawIn(BaseModel):
    players_per_team: int = Field(..., ge=2, le=30, alias="playersPerTeam")
    team_count: int = Field(default=2, ge=2, le=2)

    @root_validator(pre=True)
    def _normalize_payload(cls, values):
        data = dict(values or {})
        if data.get("players_per_team") is None and data.get("playersPerTeam") is None:
            legacy_players_per_team = data.get("team_size") or data.get("players")
            if legacy_players_per_team is not None:
                data["players_per_team"] = legacy_players_per_team
        return data


class GuestNoShowIn(BaseModel):
    justified: bool = False
    reason: Optional[str] = None


@router.post("/{match_id}/guests/{guest_id}/no-show")
def mark_guest_no_show(
    match_id: int,
    guest_id: int,
    payload: GuestNoShowIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    return svc_mark_guest_no_show(
        db,
        match=m,
        guest_id=guest_id,
        justified=bool(payload.justified),
        reason=payload.reason,
        current_user_id=current_user_id,
    )

def _ensure_match_draw_players_column(db: Session) -> None:
    """Garante compatibilidade do schema de produção antes de persistir o sorteio.

    Em alguns ambientes a migration pode ainda não ter criado a coluna JSONB
    ``players`` em ``match_draw_teams``. Como o sorteio depende dela para salvar
    membros e convidados num formato único, fazemos um self-heal idempotente.
    """
    insp = inspect(db.bind)
    try:
        if not insp.has_table("match_draw_teams"):
            return
        cols = {c["name"] for c in insp.get_columns("match_draw_teams")}
        if "players" in cols:
            return
        db.execute(text("ALTER TABLE match_draw_teams ADD COLUMN IF NOT EXISTS players JSONB"))
        db.execute(text("UPDATE match_draw_teams SET players = '[]'::jsonb WHERE players IS NULL"))
        try:
            db.execute(text("ALTER TABLE match_draw_teams ALTER COLUMN players SET NOT NULL"))
        except Exception:
            db.rollback()
            db.execute(text("ALTER TABLE match_draw_teams ADD COLUMN IF NOT EXISTS players JSONB"))
            db.execute(text("UPDATE match_draw_teams SET players = '[]'::jsonb WHERE players IS NULL"))
        db.flush()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Falha ao alinhar schema do sorteio: {exc}")


def _execute_draw_teams(
    *,
    match_id: int,
    payload: DrawIn,
    db: Session,
    current_user_id: int,
):
    """Sorteio balanceado por habilidade (1-5).

    - Considera apenas participantes/convidados CONFIRMADOS que marcaram CHEGADA.
    - Não exige pagamento para avulsos ou convidados.
    - Cria times balanceados pelo skill via snake draft.
    - Exige jogadores suficientes para ao menos 2 times.
    - Salva em match_draw_teams com team_number 1..N.
    """
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)

    _acquire_match_draw_lock(db, match_id)
    log_event(logger, "match_draw_lock_acquired", user_id=current_user_id, group_id=getattr(m, "group_id", None), match_id=match_id)

    participants = (
        db.query(MatchParticipant)
        .filter(
            MatchParticipant.match_id == match_id,
            MatchParticipant.status == ParticipantStatus.confirmed.value,
            MatchParticipant.arrived.is_(True),
        )
        .order_by(MatchParticipant.id.asc())
        .all()
    )
    guests = (
        db.query(MatchGuestPlayer)
        .filter(
            MatchGuestPlayer.match_id == match_id,
            MatchGuestPlayer.status == ParticipantStatus.confirmed.value,
            MatchGuestPlayer.arrived.is_(True),
        )
        .order_by(MatchGuestPlayer.id.asc())
        .all()
    )

    skill_by_player_id: dict[int, int] = {}
    player_name_by_id: dict[int, str] = {}
    if participants:
        pids = [int(p.player_id) for p in participants]
        pls = db.query(Player).filter(Player.id.in_(pids)).all()
        p2u = {}
        for pl in pls:
            user_id = getattr(pl, "user_id", None) or getattr(pl, "owner_id", None)
            if user_id:
                p2u[int(pl.id)] = int(user_id)
            display_name = (getattr(pl, "name", None) or getattr(getattr(pl, "user", None), "name", None) or "").strip()
            if display_name:
                player_name_by_id[int(pl.id)] = display_name
        if m.group_id and p2u:
            gms = (
                db.query(GroupMember)
                .filter(GroupMember.group_id == m.group_id)
                .filter(GroupMember.user_id.in_(list(p2u.values())))
                .all()
            )
            u2skill = {int(gm.user_id): max(1, min(5, int(getattr(gm, "skill_rating", 3) or 3))) for gm in gms if getattr(gm, "user_id", None) is not None}
            for pid, uid in p2u.items():
                skill_by_player_id[int(pid)] = int(u2skill.get(int(uid), 3) or 3)
    for p in participants:
        skill_by_player_id.setdefault(int(p.player_id), 3)
        player_name_by_id.setdefault(int(p.player_id), f"Jogador #{int(p.player_id)}")

    pool: list[dict] = []
    for p in participants:
        pid = int(p.player_id)
        pool.append({
            "kind": "member",
            "player_id": pid,
            "id": pid,
            "name": player_name_by_id.get(pid, f"Jogador #{pid}"),
            "position": getattr(p, "position", None),
            "skill": int(skill_by_player_id.get(pid, 3) or 3),
        })
    for g in guests:
        gid = int(g.id)
        pool.append({
            "kind": "guest",
            "guest_id": gid,
            "id": gid,
            "name": (g.name or f"Convidado #{gid}"),
            "position": g.position,
            "skill": max(1, min(5, int(getattr(g, "skill_rating", 3) or 3))),
        })

    pool, removed_duplicates = _deduplicate_draw_pool(pool)
    if removed_duplicates:
        log_event(
            logger,
            "match_draw_pool_deduplicated",
            user_id=current_user_id,
            group_id=getattr(m, "group_id", None),
            match_id=match_id,
            removed_duplicates=removed_duplicates,
        )

    if not pool:
        raise api_error(400, code="DRAW_NO_ELIGIBLE_PLAYERS", message="Não há participantes confirmados com chegada marcada para sortear.")

    players_per_team = int(payload.players_per_team)
    if players_per_team <= 0:
        raise api_error(422, code="DRAW_INVALID_TEAM_SIZE", message="Quantidade de jogadores por time inválida.")
    minimum_players = players_per_team * 2
    if len(pool) < minimum_players:
        raise api_error(
            400,
            code="DRAW_INSUFFICIENT_PLAYERS",
            message=f"Jogadores elegíveis insuficientes para sortear 2 times de {players_per_team}. Disponíveis atuais: {len(pool)}",
            details={"eligible_players": len(pool), "players_per_team": players_per_team},
        )

    import math
    team_count = max(2, int(math.ceil(len(pool) / players_per_team)))

    saved_rows = _saved_draw_rows(db, match_id)
    if _saved_draw_matches_current_state(saved_rows, pool, players_per_team):
        log_event(
            logger,
            "match_draw_returned_saved",
            user_id=current_user_id,
            group_id=getattr(m, "group_id", None),
            match_id=match_id,
            players_per_team=players_per_team,
        )
        return _serialize_draw_team_rows(
            db,
            m,
            saved_rows,
            current_user_id=current_user_id,
            players_per_team=players_per_team,
            eligible_players=len(pool),
            source="saved",
        )

    _ensure_match_draw_players_column(db)

    db.query(MatchDrawTeam).filter(MatchDrawTeam.match_id == match_id).delete()
    db.flush()

    pool.sort(key=lambda x: (-int(x["skill"]), x.get("name", ""), x.get("player_id", 0), x.get("guest_id", 0)))

    # Regra alinhada ao fluxo V2:
    # players_per_team é a capacidade total definida pelo Owner/Admin, incluindo goleiro.
    # Times completos são formados primeiro e a sobra compõe o último time.
    target_sizes: list[int] = []
    remaining = len(pool)
    for _ in range(team_count):
        target_size = min(players_per_team, remaining)
        target_sizes.append(target_size)
        remaining -= target_size

    teams: list[list[dict]] = [[] for _ in range(team_count)]
    team_skill_totals = [0 for _ in range(team_count)]
    team_goalkeepers = [0 for _ in range(team_count)]

    def _item_position(item: dict) -> str | None:
        return _normalize_match_position(item.get("position"))

    def _teams_with_capacity() -> list[int]:
        return [idx for idx in range(team_count) if len(teams[idx]) < target_sizes[idx]]

    def _full_size_teams(indexes: list[int]) -> list[int]:
        return [idx for idx in indexes if target_sizes[idx] == players_per_team]

    def _assign_item(item: dict, *, prioritize_goalkeeper: bool = False) -> None:
        candidates = _teams_with_capacity()
        if not candidates:
            raise api_error(500, code="DRAW_DISTRIBUTION_FAILED", message="Falha interna ao distribuir jogadores no sorteio.")
        if prioritize_goalkeeper:
            without_goalkeeper = [idx for idx in candidates if team_goalkeepers[idx] == 0]
            if without_goalkeeper:
                candidates = _full_size_teams(without_goalkeeper) or without_goalkeeper
            best_idx = min(
                candidates,
                key=lambda idx: (
                    team_goalkeepers[idx],
                    0 if target_sizes[idx] == players_per_team else 1,
                    len(teams[idx]),
                    team_skill_totals[idx],
                    idx,
                ),
            )
        else:
            candidates = _full_size_teams(candidates) or candidates
            best_idx = min(candidates, key=lambda idx: (team_skill_totals[idx], len(teams[idx]), idx))
        teams[best_idx].append(item)
        team_skill_totals[best_idx] += int(item.get("skill", 3) or 3)
        if _item_position(item) == 'goalkeeper':
            team_goalkeepers[best_idx] += 1

    goalkeepers = [item for item in pool if _item_position(item) == 'goalkeeper']
    line_players = [item for item in pool if _item_position(item) != 'goalkeeper']

    for item in goalkeepers:
        _assign_item(item, prioritize_goalkeeper=True)
    for item in line_players:
        _assign_item(item)

    draw_meta = MetaData()
    draw_table = Table("match_draw_teams", draw_meta, autoload_with=db.bind)
    draw_cols = {c.name for c in draw_table.columns}

    now = utc_now()
    for i, plist in enumerate(teams, start=1):
        values = {"match_id": match_id, "team_number": i}
        if "players" in draw_cols:
            values["players"] = plist
        if "player_ids_csv" in draw_cols:
            member_ids = [str(int(player.get("player_id") or player.get("id"))) for player in plist if (player.get("kind") or "member") == "member" and (player.get("player_id") or player.get("id")) is not None]
            values["player_ids_csv"] = ",".join(member_ids)
        if "created_at" in draw_cols:
            values["created_at"] = now
        if "updated_at" in draw_cols:
            values["updated_at"] = now
        db.execute(draw_table.insert().values(**values))
    db.commit()

    db.expire_all()
    saved_rows = _saved_draw_rows(db, match_id)
    return _serialize_draw_team_rows(
        db,
        m,
        saved_rows,
        current_user_id=current_user_id,
        players_per_team=players_per_team,
        eligible_players=len(pool),
        source="generated",
    )


@router.post("/{match_id}/draw", response_model=DrawResponseOut)
def draw_teams(
    match_id: int,
    payload: DrawIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    result = _execute_draw_teams(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)
    log_event(logger, "match_draw_executed", user_id=current_user_id, match_id=match_id, players_per_team=payload.players_per_team)
    return result


@group_router.post("/{match_id}/draw", response_model=DrawResponseOut)
def draw_teams_in_group(
    group_id: str,
    match_id: int,
    payload: DrawIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    result = _execute_draw_teams(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)
    log_event(logger, "match_draw_executed", user_id=current_user_id, group_id=group_id, match_id=match_id, players_per_team=payload.players_per_team)
    return result


@router.get("/{match_id}/draw", response_model=DrawResponseOut)
def get_saved_draw(
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if m.group_id:
        get_group_member(db, m.group_id, current_user_id)
    elif int(m.owner_id) != int(current_user_id):
        raise HTTPException(status_code=403, detail="Sem permissão")
    return _get_saved_draw_response(db, m, current_user_id=current_user_id)


@group_router.get("/{match_id}/draw", response_model=DrawResponseOut)
def get_saved_draw_in_group(
    group_id: str,
    match_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    get_group_member(db, group_id, current_user_id)
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return _get_saved_draw_response(db, m, current_user_id=current_user_id)


class PromoteWaitlistIn(BaseModel):
    count: int = Field(default=1, ge=1, le=100)


@router.post("/{match_id}/waitlist/promote", response_model=PresenceOut)
def promote_waitlist(
    match_id: int,
    payload: PromoteWaitlistIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)
    return promote_waitlist_entries(
        db,
        match=m,
        current_user_id=current_user_id,
        limit=payload.count,
    )


class CloseMatchIn(BaseModel):
    generate_charges: bool = True
    generate_venue: bool = True


@router.post("/{match_id}/close")
def close_match(
    match_id: int,
    payload: CloseMatchIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    """Fecha a partida e (opcionalmente) gera cobranças automáticas.

    Regras:
    - Se for partida de grupo: apenas ADM/Owner do grupo podem fechar
    - Gera cobranças 'single' para participantes confirmados que são avulsos (billing_type=single)
    - Pode gerar 'venue' como despesa do grupo (valor da quadra)
    - Idempotente por constraints
    """
    m = _require_match(db, match_id)
    _require_can_manage_match(db, m, current_user_id)

    if not m.group_id:
        # match sem grupo: apenas marca status
        m.status = "closed"
        db.add(m)
        db.commit()
        db.refresh(m)
        return {"ok": True, "match_id": match_id, "group_id": None, "created": 0, "skipped": 0}

    group = db.query(Group).filter(Group.id == m.group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    # marca como fechado
    m.status = "closed"
    db.add(m)

    created = 0
    skipped = 0

    if payload.generate_charges:
        # valor por jogo (prioridade: single_cost > per_person_cost > match.price)
        amount = float(getattr(group, "single_cost", 0) or 0)
        if amount <= 0:
            amount = float(getattr(group, "per_person_cost", 0) or 0)
        if amount <= 0 and getattr(m, "price_cents", None):
            amount = float((m.price_cents or 0) / 100.0)

        amount_cents = _cents(amount)
        due = (m.starts_at.date() if m.starts_at else None)

        confirmed_parts = (
            db.query(MatchParticipant)
            .filter(MatchParticipant.match_id == match_id)
            .filter(MatchParticipant.status == ParticipantStatus.confirmed.value)
            .all()
        )

        # Fase 4: quando já houve controle de presença (arrived/no_show), cobrar apenas quem realmente jogou.
        # Compat: se ninguém foi marcado ainda, mantém o comportamento legado e usa todos os confirmados.
        has_attendance_marks = any(bool(getattr(p, "arrived", False) or getattr(p, "no_show", False)) for p in confirmed_parts)
        participants = [
            p for p in confirmed_parts
            if (not has_attendance_marks) or (bool(getattr(p, "arrived", False)) and not bool(getattr(p, "no_show", False)))
        ]

        # map player_id -> user_id
        player_ids = [p.player_id for p in participants]
        players = db.query(Player).filter(Player.id.in_(player_ids)).all() if player_ids else []
        p2u = {pl.id: getattr(pl, "owner_id", None) or getattr(pl, "user_id", None) for pl in players}

        # billing por user no grupo
        uids = [uid for uid in p2u.values() if uid is not None]
        memberships = (
            db.query(GroupMember)
            .filter(GroupMember.group_id == m.group_id)
            .filter(GroupMember.user_id.in_(uids))
            .filter(GroupMember.status == "active")
            .all()
        )
        billing = {gm.user_id: (gm.billing_type or "single") for gm in memberships}

        for p in participants:
            uid = p2u.get(p.player_id)
            if not uid:
                continue
            if (billing.get(uid) or "single") != "single":
                continue
            if amount_cents == 0:
                continue

            exists = (
                db.query(GroupFinancialEntry)
                .filter(GroupFinancialEntry.group_id == m.group_id)
                .filter(GroupFinancialEntry.user_id == uid)
                .filter(GroupFinancialEntry.entry_type == "single")
                .filter(GroupFinancialEntry.match_id == match_id)
                .first()
            )
            if exists:
                skipped += 1
                continue

            e = GroupFinancialEntry(
                group_id=m.group_id,
                user_id=uid,
                match_id=match_id,
                entry_type="single",
                amount_cents=amount_cents,
                currency=(group.currency or "BRL"),
                status="pending",
                due_date=due,
                description=f"Avulso - partida #{match_id}",
                paid=False,
                paid_at=None,
                confirmed_by_user_id=None,
            )
            db.add(e)
            created += 1

    if payload.generate_venue:
        venue = float(getattr(group, "venue_cost", 0) or 0)
        venue_cents = abs(_cents(venue)) if venue > 0 else 0
        if venue_cents != 0:
            exists = (
                db.query(GroupFinancialEntry)
                .filter(GroupFinancialEntry.group_id == m.group_id)
                .filter(GroupFinancialEntry.user_id.is_(None))
                .filter(GroupFinancialEntry.entry_type == "venue")
                .filter(GroupFinancialEntry.match_id == match_id)
                .first()
            )
            if not exists:
                e = GroupFinancialEntry(
                    group_id=m.group_id,
                    user_id=None,
                    match_id=match_id,
                    entry_type="venue",
                    amount_cents=venue_cents,
                    currency=(group.currency or "BRL"),
                    status="pending",
                    due_date=(m.starts_at.date() if m.starts_at else None),
                    description=f"Quadra - partida #{match_id}",
                    paid=False,
                    paid_at=None,
                    confirmed_by_user_id=None,
                )
                db.add(e)
                created += 1
            else:
                skipped += 1

    db.commit()
    return {"ok": True, "match_id": match_id, "group_id": m.group_id, "created": created, "skipped": skipped}


class NoShowIn(BaseModel):
    justified: bool = False
    reason: Optional[str] = None


@router.post("/{match_id}/participants/{player_id}/no-show")
def mark_no_show(
    match_id: int,
    player_id: int,
    payload: NoShowIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    return mark_member_no_show(
        db,
        match=m,
        player_id=player_id,
        justified=bool(payload.justified),
        reason=payload.reason,
        current_user_id=current_user_id,
    )


@group_router.post("/{match_id}/waitlist/promote", response_model=PresenceOut)
def promote_waitlist_in_group(
    group_id: str,
    match_id: int,
    payload: PromoteWaitlistIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return promote_waitlist(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/close")
def close_match_in_group(
    group_id: str,
    match_id: int,
    payload: CloseMatchIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return close_match(match_id=match_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/participants/{player_id}/no-show")
def mark_no_show_in_group(
    group_id: str,
    match_id: int,
    player_id: int,
    payload: NoShowIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return mark_no_show(match_id=match_id, player_id=player_id, payload=payload, db=db, current_user_id=current_user_id)


@group_router.post("/{match_id}/guests/{guest_id}/no-show")
def mark_guest_no_show_in_group(
    group_id: str,
    match_id: int,
    guest_id: int,
    payload: GuestNoShowIn,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user),
):
    m = _require_match(db, match_id)
    if str(m.group_id) != str(group_id):
        raise HTTPException(status_code=404, detail="Match não encontrado no grupo")
    return mark_guest_no_show(match_id=match_id, guest_id=guest_id, payload=payload, db=db, current_user_id=current_user_id)


# -----------------------------
# Phase 6 / 7 - Game Flow + Stats
# -----------------------------
class GoalIn(BaseModel):
    team: int
    player_id: Optional[int] = None
    guest_id: Optional[int] = None
    minute: Optional[int] = None
