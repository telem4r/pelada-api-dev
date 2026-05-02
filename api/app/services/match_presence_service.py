from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import Group, GroupFinancialEntry, GroupMember, Match, MatchGuestPlayer, MatchParticipant, ParticipantStatus, Player, User
from app.permissions import get_group_member, get_user_primary_player
from app.repositories.matches import (
    capacity_ok,
    confirmed_count,
    delete_guest_presence,
    delete_member_presence,
    find_existing_participant,
    list_match_guests,
    list_match_participants,
    next_queue_position,
    total_confirmed_count,
    upsert_participant,
)
from app.services.finance_snapshot_service import rebuild_snapshot
from app.communication_utils import create_notification, notification_allowed
from app.core.time import utc_now


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


def _confirmed_position_counts(db: Session, match_id: int) -> dict[str, int]:
    counts = {'line': 0, 'goalkeeper': 0}
    parts = list_match_participants(db, match_id)
    for part in parts:
        if part.status != ParticipantStatus.confirmed.value:
            continue
        pos = _normalize_match_position(getattr(part, 'position', None)) or 'line'
        counts[pos] = counts.get(pos, 0) + 1
    for row in list_match_guests(db, match_id):
        guest = _guest_row_to_out(row, fallback_match_id=match_id)
        if guest['status'] != ParticipantStatus.confirmed.value:
            continue
        pos = _normalize_match_position(guest.get('position')) or 'line'
        counts[pos] = counts.get(pos, 0) + 1
    return counts




def _lock_match_row(db: Session, match_id: int) -> Match:
    locked = (
        db.query(Match)
        .filter(Match.id == match_id)
        .with_for_update()
        .first()
    )
    if locked is None:
        raise HTTPException(status_code=404, detail="Partida não encontrada")
    return locked


def _position_capacity_remaining(match: Match, counts: dict[str, int], position: str | None) -> int | None:
    normalized = _normalize_match_position(position) or 'line'
    if normalized == 'goalkeeper':
        slots = int(getattr(match, 'goalkeeper_slots', 0) or 0)
        if slots <= 0:
            return None
        return max(0, slots - int(counts.get('goalkeeper', 0)))
    slots = int(getattr(match, 'line_slots', 0) or 0)
    if slots <= 0:
        return None
    return max(0, slots - int(counts.get('line', 0)))


def _position_capacity_ok(match: Match, counts: dict[str, int], position: str | None) -> bool:
    remaining = _position_capacity_remaining(match, counts, position)
    return remaining is None or remaining > 0

def _normalize_group_type(value: str | None) -> str:
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


def _normalize_billing_type(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"monthly", "mensal", "mensalista"}:
        return "monthly"
    if raw in {"single", "avulso", "casual"}:
        return "single"
    return raw


def _effective_group_billing_type(group_type: str | None, role: str | None, billing_type: str | None) -> str:
    normalized_group_type = _normalize_group_type(group_type)
    normalized_role = (role or "").strip().lower()
    normalized_billing = _normalize_billing_type(billing_type)

    if normalized_group_type == "avulso":
        return "single"

    if normalized_group_type == "hibrido" and normalized_role in {"owner", "admin"}:
        return "monthly"

    if normalized_billing in {"monthly", "single"}:
        return normalized_billing

    return "single"


def _is_monthly_adimplente(db: Session, group_id: str, user_id: int, ref_dt: Optional[datetime] = None) -> bool:
    ref = ref_dt or utc_now()
    y, m = ref.year, ref.month

    # Hotfix financeiro híbrido:
    # ausência de mensalidade real no período NÃO é inadimplência.
    # Só obrigações reais abertas/parciais/pendentes/vencidas do mês de referência bloqueiam presença.
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

    from sqlalchemy import text as sa_text
    player_row = db.execute(sa_text(
        "select p.id::text from public.players p join public.users u on u.id = p.user_id where u.id = :uid limit 1"
    ), {'uid': user_id}).first()
    if player_row:
        pid = player_row[0]
        v2_unpaid = db.execute(sa_text("""
            select 1 from public.finance_obligations_v2
            where group_id = cast(:gid as uuid) and player_id = cast(:pid as uuid)
              and source_type = 'mensalidade'
              and lower(coalesce(status, '')) in ('aberta','open','parcial','partial','pendente','pending','vencida','overdue')
              and deleted_at is null
              and competence_month = :m and competence_year = :y
            limit 1
        """), {'gid': group_id, 'pid': pid, 'm': m, 'y': y}).first()
        if v2_unpaid is not None:
            return False
    return True


def _match_start_now_reference(match: Match) -> datetime:
    starts_at = getattr(match, "starts_at", None) or getattr(match, "date_time", None)
    if starts_at is None:
        return utc_now()
    tz = getattr(starts_at, "tzinfo", None)
    return datetime.now(tz) if tz is not None else utc_now()


def auto_release_waitlist(db: Session, match: Match, group: Optional[Group] = None) -> bool:
    if not match.group_id:
        return False
    grp = group or db.query(Group).filter(Group.id == match.group_id).first()
    if not grp:
        return False
    group_type = _normalize_group_type(getattr(grp, 'group_type', None))
    if group_type != 'hibrido':
        return False
    days = int(getattr(match, "single_waitlist_release_days", 0) or 0)
    if days <= 0:
        days = int(getattr(grp, "single_waitlist_release_days", 0) or 0)
    if days <= 0:
        return False
    starts_at = getattr(match, "starts_at", None)
    if starts_at is None:
        return False
    now = datetime.now(starts_at.tzinfo) if getattr(starts_at, "tzinfo", None) is not None else utc_now()
    delta_days = (starts_at - now).total_seconds() / 86400.0
    if delta_days > days:
        return False

    changed = False
    while capacity_ok(match, total_confirmed_count(db, match.id)):
        counts = _confirmed_position_counts(db, match.id)
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
        promoted = False
        for part in waiters:
            if not _position_capacity_ok(match, counts, getattr(part, 'position', None)):
                continue
            part.status = ParticipantStatus.confirmed.value
            part.queue_position = None
            db.add(part)
            db.flush()
            changed = True
            promoted = True
            break
        if promoted:
            continue

        guests = list_match_guests(db, match.id)
        waiting_guest = None
        for row in guests:
            guest_out = _guest_row_to_out(row, fallback_match_id=match.id)
            if guest_out["status"] != ParticipantStatus.waitlist.value:
                continue
            if not _position_capacity_ok(match, counts, guest_out.get('position')):
                continue
            waiting_guest = guest_out
            break
        if waiting_guest is None:
            break
        from app.repositories.matches import guest_columns, guest_table
        table = guest_table(db)
        cols = guest_columns(db)
        values = {"status": ParticipantStatus.confirmed.value}
        if "updated_at" in cols:
            values["updated_at"] = utc_now()
        db.execute(table.update().where(table.c.id == int(waiting_guest["id"])).values(**values))
        db.flush()
        changed = True
    return changed


def promote_next_waiting(db: Session, match: Match) -> bool:
    if not capacity_ok(match, total_confirmed_count(db, match.id)):
        return False
    counts = _confirmed_position_counts(db, match.id)
    queue = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match.id)
        .filter(MatchParticipant.status == ParticipantStatus.waitlist.value)
        .filter(MatchParticipant.requires_approval.is_(False))
        .order_by(MatchParticipant.waitlist_tier.asc(), MatchParticipant.queue_position.asc().nullslast(), MatchParticipant.id.asc())
        .all()
    )
    for candidate in queue:
        if not _position_capacity_ok(match, counts, getattr(candidate, 'position', None)):
            continue
        candidate.status = ParticipantStatus.confirmed.value
        candidate.queue_position = None
        db.add(candidate)
        db.flush()
        return True

    guests = list_match_guests(db, match.id)
    waiting_guest = None
    for row in guests:
        guest_out = _guest_row_to_out(row, fallback_match_id=match.id)
        if guest_out["status"] != ParticipantStatus.waitlist.value:
            continue
        if not _position_capacity_ok(match, counts, guest_out.get('position')):
            continue
        waiting_guest = guest_out
        break
    if waiting_guest is None:
        return False
    from app.repositories.matches import guest_columns, guest_table
    table = guest_table(db)
    cols = guest_columns(db)
    values = {"status": ParticipantStatus.confirmed.value}
    if "updated_at" in cols:
        values["updated_at"] = utc_now()
    db.execute(table.update().where(table.c.id == int(waiting_guest["id"])).values(**values))
    db.flush()
    return True




def _match_charge_amount_cents(match: Match) -> int:
    return abs(int(getattr(match, "price_cents", 0) or 0))


def _group_currency(group: Group | None) -> str:
    return (getattr(group, "currency", None) or "BRL").strip().upper()


def _get_guest_name(db: Session, *, match_id: int, guest_id: int) -> str | None:
    row = (
        db.query(MatchGuestPlayer)
        .filter(MatchGuestPlayer.match_id == match_id)
        .filter(MatchGuestPlayer.id == guest_id)
        .first()
    )
    if row is not None:
        name = (getattr(row, "name", None) or "").strip()
        if name:
            return name
    for raw in list_match_guests(db, match_id):
        out = _guest_row_to_out(raw, fallback_match_id=match_id)
        if int(out["id"]) == int(guest_id):
            name = (out.get("name") or "").strip()
            if name:
                return name
    return None


def _get_guest_payment_entry(db: Session, *, match: Match, guest_id: int):
    notes_guest_key = f"guest_id:{guest_id}"
    notes_match_key = f"match_id:{match.id}"
    return (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == match.group_id)
        .filter(GroupFinancialEntry.user_id.is_(None))
        .filter(
            or_(
                and_(
                    GroupFinancialEntry.entry_type == "single_guest",
                    GroupFinancialEntry.notes.isnot(None),
                    GroupFinancialEntry.notes.like(f"%{notes_guest_key}%"),
                    GroupFinancialEntry.notes.like(f"%{notes_match_key}%"),
                ),
                and_(
                    GroupFinancialEntry.match_id == match.id,
                    GroupFinancialEntry.entry_type == "single",
                    or_(
                        GroupFinancialEntry.notes == notes_guest_key,
                        GroupFinancialEntry.notes.like(f"{notes_guest_key};%"),
                        GroupFinancialEntry.description == f"Convidado #{guest_id} - partida #{match.id}",
                    ),
                ),
            )
        )
        .order_by(GroupFinancialEntry.id.asc())
        .first()
    )


def _sync_member_payment_entry(
    db: Session,
    *,
    match: Match,
    group: Group,
    player_id: int,
    paid: bool,
    acting_user_id: int,
) -> None:
    participant = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match.id, MatchParticipant.player_id == player_id)
        .first()
    )
    if participant is None:
        raise HTTPException(status_code=404, detail="Participante não encontrado")
    player = db.query(Player).filter(Player.id == player_id).first()
    user_id = (getattr(player, "owner_id", None) or getattr(player, "user_id", None)) if player else None
    if user_id is None:
        return
    membership = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == match.group_id, GroupMember.user_id == int(user_id))
        .first()
    )
    billing_type = (getattr(membership, "billing_type", None) or "single").lower()
    if billing_type != "single":
        return

    amount_cents = _match_charge_amount_cents(match)
    due = match.starts_at.date() if getattr(match, "starts_at", None) else utc_now().date()
    entry = (
        db.query(GroupFinancialEntry)
        .filter(GroupFinancialEntry.group_id == match.group_id)
        .filter(GroupFinancialEntry.user_id == int(user_id))
        .filter(GroupFinancialEntry.entry_type == "single")
        .filter(GroupFinancialEntry.match_id == match.id)
        .first()
    )
    if entry is None and amount_cents <= 0:
        return
    if entry is None:
        entry = GroupFinancialEntry(
            group_id=match.group_id,
            user_id=int(user_id),
            match_id=match.id,
            entry_type="single",
            amount_cents=amount_cents,
            currency=_group_currency(group),
            status="pending",
            due_date=due,
            description=f"Avulso - partida #{match.id}",
            paid=False,
            paid_amount_cents=0,
            confirmed_by_user_id=None,
        )
    else:
        if amount_cents > 0:
            entry.amount_cents = amount_cents
        entry.currency = _group_currency(group)
        entry.due_date = due
        if not entry.description:
            entry.description = f"Avulso - partida #{match.id}"

    if paid:
        entry.paid = True
        entry.status = "paid"
        entry.paid_at = utc_now()
        entry.paid_amount_cents = abs(int(entry.amount_cents or amount_cents or 0))
        entry.confirmed_by_user_id = acting_user_id
    else:
        entry.paid = False
        entry.status = "pending"
        entry.paid_at = None
        entry.paid_amount_cents = 0
        entry.confirmed_by_user_id = None
    db.add(entry)


def _sync_guest_payment_entry(
    db: Session,
    *,
    match: Match,
    group: Group,
    guest_id: int,
    paid: bool,
    acting_user_id: int,
) -> None:
    amount_cents = _match_charge_amount_cents(match)
    due = match.starts_at.date() if getattr(match, "starts_at", None) else utc_now().date()
    guest_name = _get_guest_name(db, match_id=match.id, guest_id=guest_id) or f"#{guest_id}"
    guest_desc = f"Convidado {guest_name} - partida #{match.id}"
    guest_notes = f"guest_id:{guest_id};guest_name:{guest_name};match_id:{match.id}"
    entry = _get_guest_payment_entry(db, match=match, guest_id=guest_id)
    if entry is None and amount_cents <= 0:
        return
    if entry is None:
        entry = GroupFinancialEntry(
            group_id=match.group_id,
            user_id=None,
            match_id=None,
            entry_type="single_guest",
            amount_cents=amount_cents,
            currency=_group_currency(group),
            status="pending",
            due_date=due,
            description=guest_desc,
            notes=guest_notes,
            paid=False,
            paid_amount_cents=0,
            confirmed_by_user_id=None,
        )
    else:
        if amount_cents > 0:
            entry.amount_cents = amount_cents
        entry.currency = _group_currency(group)
        entry.match_id = None if (entry.entry_type or "").lower() == "single_guest" else entry.match_id
        entry.due_date = due
        entry.description = guest_desc
        entry.notes = guest_notes
        if not getattr(entry, "entry_type", None):
            entry.entry_type = "single_guest"

    if paid:
        entry.paid = True
        entry.status = "paid"
        entry.paid_at = utc_now()
        entry.paid_amount_cents = abs(int(entry.amount_cents or amount_cents or 0))
        entry.confirmed_by_user_id = acting_user_id
    else:
        entry.paid = False
        entry.status = "pending"
        entry.paid_at = None
        entry.paid_amount_cents = 0
        entry.confirmed_by_user_id = None
    db.add(entry)

def build_presence(db: Session, *, match: Match, current_user_id: int) -> dict:
    if match.group_id:
        get_group_member(db, match.group_id, current_user_id)
    elif match.owner_id != current_user_id and not match.is_public:
        raise HTTPException(status_code=403, detail="Sem permissão")
    if match.group_id:
        grp = db.query(Group).filter(Group.id == match.group_id).first()
        auto_release_waitlist(db, match, grp)
        db.flush()
    parts = list_match_participants(db, match.id)
    guests = list_match_guests(db, match.id)
    pids = [p.player_id for p in parts]
    players = db.query(Player).filter(Player.id.in_(pids)).all() if pids else []
    p2u = {pl.id: getattr(pl, "owner_id", None) or getattr(pl, "user_id", None) for pl in players if (getattr(pl, "owner_id", None) or getattr(pl, "user_id", None))}
    uids = [u for u in p2u.values() if u]
    users = db.query(User).filter(User.id.in_(uids)).all() if uids else []
    u_by_id = {u.id: u for u in users}
    def _user_name(uid: int) -> str:
        user = u_by_id.get(uid)
        if not user:
            return "Jogador"
        first = getattr(user, "first_name", None)
        last = getattr(user, "last_name", None)
        if isinstance(first, str) or isinstance(last, str):
            full = f"{(first or '').strip()} {(last or '').strip()}".strip()
            if full:
                return full
        name = getattr(user, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return "Jogador"
    confirmed: list[dict] = []
    waiting: list[dict] = []
    membership_map: dict[int, str | None] = {}
    if match.group_id and uids:
        memberships = (
            db.query(GroupMember)
            .filter(GroupMember.group_id == match.group_id)
            .filter(GroupMember.user_id.in_(uids))
            .all()
        )
        membership_map = {int(m.user_id): _effective_group_billing_type(getattr(grp, "group_type", None) if grp else None, getattr(m, "role", None), getattr(m, "billing_type", None)) for m in memberships}
    for part in parts:
        uid = p2u.get(part.player_id)
        item = {
            "kind": "member",
            "id": part.player_id,
            "name": _user_name(uid) if uid else "Jogador",
            "presence": "confirmed" if part.status == ParticipantStatus.confirmed.value else "waiting",
            "arrived": bool(part.arrived),
            "paid": bool(part.paid),
            "no_show": bool(getattr(part, "no_show", False)),
            "no_show_justified": bool(getattr(part, "no_show_justified", False)),
            "no_show_reason": getattr(part, "no_show_reason", None),
            "billing_type": membership_map.get(int(uid)) if uid else None,
            "requires_approval": bool(getattr(part, "requires_approval", False)),
            "queue_position": getattr(part, "queue_position", None),
            "position": _position_label(getattr(part, "position", None)) or getattr(part, "position", None),
        }
        (confirmed if part.status == ParticipantStatus.confirmed.value else waiting).append(item)
    guest_paid_map: dict[int, bool] = {}
    if match.group_id and guests:
        guest_entries = (
            db.query(GroupFinancialEntry)
            .filter(GroupFinancialEntry.group_id == match.group_id)
            .filter(GroupFinancialEntry.user_id.is_(None))
            .filter(
                or_(
                    and_(
                        GroupFinancialEntry.entry_type == "single_guest",
                        GroupFinancialEntry.notes.isnot(None),
                        GroupFinancialEntry.notes.like(f"%match_id:{match.id}%"),
                    ),
                    and_(
                        GroupFinancialEntry.match_id == match.id,
                        GroupFinancialEntry.entry_type == "single",
                    ),
                )
            )
            .all()
        )
        for entry in guest_entries:
            guest_id = None
            notes = (getattr(entry, "notes", None) or "")
            if "guest_id:" in notes:
                try:
                    guest_id = int(notes.split("guest_id:", 1)[1].split(";", 1)[0].strip())
                except Exception:
                    guest_id = None
            if guest_id is None:
                desc = (entry.description or "")
                if desc.startswith("Convidado #") and " - partida #" in desc:
                    try:
                        guest_id = int(desc.split("#", 1)[1].split(" ", 1)[0])
                    except Exception:
                        guest_id = None
            if guest_id is not None:
                guest_paid_map[int(guest_id)] = (entry.status or "pending").lower() == "paid"
    for row in guests:
        gout = _guest_row_to_out(row, fallback_match_id=match.id)
        is_waiting = gout["status"] in {ParticipantStatus.waitlist.value, "waiting"}
        item = {
            "kind": "guest",
            "id": gout["id"],
            "name": gout["name"],
            "presence": "waiting" if is_waiting else "confirmed",
            "arrived": bool(gout["arrived"]),
            "paid": bool(guest_paid_map.get(int(gout["id"]), False)),
            "no_show": bool(gout["no_show"]),
            "no_show_justified": bool(gout["no_show_justified"]),
            "no_show_reason": gout["no_show_reason"],
            "billing_type": None,
            "requires_approval": False,
            "queue_position": None,
            "position": _position_label(gout.get("position")) or gout.get("position"),
        }
        (waiting if is_waiting else confirmed).append(item)
    return {"match_id": match.id, "confirmed": confirmed, "waiting": waiting}


def confirm_presence_for_user(db: Session, *, match: Match, current_user_id: int, position: str) -> dict:
    if not match.group_id:
        raise HTTPException(status_code=400, detail="Match não pertence a um grupo")
    match = _lock_match_row(db, match.id)
    group, membership = get_group_member(db, match.group_id, current_user_id)
    auto_release_waitlist(db, match, group)
    normalized_position = _normalize_match_position(position)
    if normalized_position is None:
        raise HTTPException(status_code=422, detail='Posição inválida. Use Linha ou Gol.')

    player = get_user_primary_player(db, current_user_id)
    existing = find_existing_participant(db, match.id, player.id, current_user_id)
    group_type = _normalize_group_type(group.group_type)
    billing = _effective_group_billing_type(
        group.group_type,
        getattr(membership, "role", None),
        getattr(membership, "billing_type", None) or "single",
    )
    is_monthly = billing == "monthly"
    is_adimplente = _is_monthly_adimplente(db, group.id, current_user_id, ref_dt=match.starts_at) if is_monthly else True

    current_confirmed = total_confirmed_count(db, match.id)
    cap_ok = capacity_ok(match, current_confirmed)
    position_counts = _confirmed_position_counts(db, match.id)
    line_slots = int(getattr(match, 'line_slots', 0) or 0)
    goalkeeper_slots = int(getattr(match, 'goalkeeper_slots', 0) or 0)
    position_cap_ok = True

    if normalized_position == 'line' and line_slots > 0:
        current_line = position_counts.get('line', 0)
        if existing is not None and getattr(existing, 'status', None) == ParticipantStatus.confirmed.value and (_normalize_match_position(getattr(existing, 'position', None)) or 'line') == 'line':
            current_line = max(0, current_line - 1)
        position_cap_ok = current_line < line_slots
    if normalized_position == 'goalkeeper' and goalkeeper_slots > 0:
        current_goalkeepers = position_counts.get('goalkeeper', 0)
        if existing is not None and getattr(existing, 'status', None) == ParticipantStatus.confirmed.value and _normalize_match_position(getattr(existing, 'position', None)) == 'goalkeeper':
            current_goalkeepers = max(0, current_goalkeepers - 1)
        position_cap_ok = current_goalkeepers < goalkeeper_slots

    status = ParticipantStatus.confirmed.value if (cap_ok and position_cap_ok) else ParticipantStatus.waitlist.value
    waitlist_tier = 0
    requires_approval = False

    # Regra crítica do grupo híbrido:
    # - mensalista adimplente pode entrar direto se houver vaga
    # - mensalista inadimplente vai para espera no fim da fila e precisa aprovação
    # - avulso SEMPRE entra na espera, respeitando a ordem de chegada
    if group_type == "hibrido":
        if is_monthly and is_adimplente:
            status = ParticipantStatus.confirmed.value if (cap_ok and position_cap_ok) else ParticipantStatus.waitlist.value
        elif is_monthly and not is_adimplente:
            status = ParticipantStatus.waitlist.value
            waitlist_tier = 1
            requires_approval = True
        else:
            status = ParticipantStatus.waitlist.value
            waitlist_tier = 0
            requires_approval = False

    elif not cap_ok or not position_cap_ok:
        status = ParticipantStatus.waitlist.value

    queue_position = None
    if status == ParticipantStatus.waitlist.value:
        current_q = getattr(existing, "queue_position", None) if existing else None
        queue_position = current_q if current_q is not None else next_queue_position(db, match.id, waitlist_tier)

    try:
        upsert_participant(
            db,
            match_id=match.id,
            player_id=player.id,
            user_id=current_user_id,
            status=status,
            waitlist_tier=waitlist_tier,
            requires_approval=requires_approval,
            queue_position=queue_position,
            position=normalized_position,
        )
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao confirmar presença: {exc.__class__.__name__}")

    db.refresh(match)
    return build_presence(db, match=match, current_user_id=current_user_id)


def remove_presence_entry(
    db: Session,
    *,
    match: Match,
    current_user_id: int,
    player_id: int | None,
    target: str | None,
    target_id: int | None,
) -> dict:
    match = _lock_match_row(db, match.id)
    actor_player = get_user_primary_player(db, current_user_id)
    is_manager = False
    if match.group_id:
        _, gm = get_group_member(db, match.group_id, current_user_id)
        is_manager = (getattr(gm, "role", "") or "").lower() in {"owner", "admin"}
    else:
        is_manager = match.owner_id == current_user_id
    raw_target = (target or "member").strip().lower()
    resolved_target_id = target_id or player_id
    try:
        if raw_target == "guest":
            if not is_manager:
                raise HTTPException(status_code=403, detail="Apenas owner/admin pode remover convidado")
            if not resolved_target_id:
                raise HTTPException(status_code=400, detail="target_id do convidado é obrigatório")
            removed = delete_guest_presence(db, match.id, int(resolved_target_id))
            if not removed:
                raise HTTPException(status_code=404, detail="Convidado não encontrado")
        else:
            target_player_id = int(resolved_target_id or actor_player.id)
            if target_player_id != int(actor_player.id) and not is_manager:
                raise HTTPException(status_code=403, detail="Sem permissão para remover outro jogador")
            if target_player_id == int(actor_player.id) and not is_manager:
                starts_at = getattr(match, "starts_at", None) or getattr(match, "date_time", None)
                if starts_at is not None:
                    cutoff = starts_at - timedelta(hours=2)
                    if _match_start_now_reference(match) > cutoff:
                        raise HTTPException(status_code=403, detail="Não é permitido sair da lista com menos de 2 horas para o início da partida")
            removed = delete_member_presence(db, match.id, target_player_id)
            if not removed:
                raise HTTPException(status_code=404, detail="Participante não encontrado")
        promote_next_waiting(db, match)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError as exc:
        db.rollback()
        detail = "Erro ao remover convidado" if raw_target == "guest" else "Erro ao remover participante"
        raise HTTPException(status_code=500, detail=f"{detail}: {exc.__class__.__name__}")
    return build_presence(db, match=match, current_user_id=current_user_id)


def approve_member_presence_entry(db: Session, *, match: Match, player_id: int, current_user_id: int, position: str | None = None) -> dict:
    from app.permissions import require_group_admin
    match = _lock_match_row(db, match.id)
    if match.group_id:
        require_group_admin(db, match.group_id, current_user_id)
    elif match.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Sem permissão")
    part = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match.id)
        .filter(MatchParticipant.player_id == player_id)
        .with_for_update()
        .first()
    )
    if not part:
        raise HTTPException(status_code=404, detail="Participante não encontrado")
    normalized_position = _normalize_match_position(position) if position is not None else _normalize_match_position(getattr(part, 'position', None))
    if normalized_position is None:
        raise HTTPException(status_code=422, detail="Defina a posição do jogador antes de aprovar")

    current_total = total_confirmed_count(db, match.id)
    cap_ok = capacity_ok(match, current_total)
    counts = _confirmed_position_counts(db, match.id)
    if getattr(part, 'status', None) == ParticipantStatus.confirmed.value:
        current_pos = _normalize_match_position(getattr(part, 'position', None)) or 'line'
        counts[current_pos] = max(0, counts.get(current_pos, 0) - 1)
        current_total = max(0, current_total - 1)
        cap_ok = capacity_ok(match, current_total)
    if not cap_ok or not _position_capacity_ok(match, counts, normalized_position):
        raise HTTPException(status_code=409, detail="Sem vaga disponível para confirmar este jogador na posição selecionada")

    part.position = normalized_position
    part.status = ParticipantStatus.confirmed.value
    part.requires_approval = False
    part.waitlist_tier = 0
    part.queue_position = None
    db.add(part)
    db.commit()
    return build_presence(db, match=match, current_user_id=current_user_id)


def admin_mark_presence(
    db: Session,
    *,
    match: Match,
    current_user_id: int,
    target: str,
    target_id: int,
    arrived: bool | None,
    paid: bool | None,
) -> dict:
    if match.group_id:
        from app.permissions import require_group_admin
        require_group_admin(db, match.group_id, current_user_id)
    elif match.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Sem permissão")
    tgt = (target or "").lower().strip()
    group = db.query(Group).filter(Group.id == match.group_id).first() if match.group_id else None
    if tgt == "member":
        part = (
            db.query(MatchParticipant)
            .filter(MatchParticipant.match_id == match.id)
            .filter(MatchParticipant.player_id == target_id)
            .first()
        )
        if not part:
            raise HTTPException(status_code=404, detail="Participante não encontrado")
        if arrived is not None:
            part.arrived = bool(arrived)
            if bool(arrived):
                part.no_show = False
                part.no_show_justified = False
                part.no_show_reason = None
        if paid is not None:
            part.paid = bool(paid)
            if group is not None:
                _sync_member_payment_entry(
                    db,
                    match=match,
                    group=group,
                    player_id=target_id,
                    paid=bool(paid),
                    acting_user_id=current_user_id,
                )
        db.add(part)
    elif tgt == "guest":
        from app.services.match_guest_service import set_guest_flags
        guest = set_guest_flags(db, match_id=match.id, guest_id=target_id, arrived=arrived)
        if guest is None:
            raise HTTPException(status_code=404, detail="Convidado não encontrado")
        if paid is not None and group is not None:
            _sync_guest_payment_entry(
                db,
                match=match,
                group=group,
                guest_id=target_id,
                paid=bool(paid),
                acting_user_id=current_user_id,
            )
    else:
        raise HTTPException(status_code=400, detail="target inválido")
    db.commit()
    if group is not None:
        rebuild_snapshot(db, group_id=str(group.id), reference_month=(match.starts_at or utc_now()).date().replace(day=1))
        db.commit()
    return build_presence(db, match=match, current_user_id=current_user_id)


def mark_member_no_show(
    db: Session,
    *,
    match: Match,
    player_id: int,
    justified: bool,
    reason: str | None,
    current_user_id: int,
) -> dict:
    if match.group_id:
        from app.permissions import require_group_admin
        require_group_admin(db, match.group_id, current_user_id)
    elif match.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Sem permissão")
    part = (
        db.query(MatchParticipant)
        .filter(MatchParticipant.match_id == match.id)
        .filter(MatchParticipant.player_id == player_id)
        .first()
    )
    if not part:
        raise HTTPException(status_code=404, detail="Participante não encontrado")
    part.arrived = False
    part.no_show = True
    part.no_show_justified = bool(justified)
    part.no_show_reason = reason
    part.paid = False
    db.add(part)
    fine_created = False
    if match.group_id and not justified:
        group = db.query(Group).filter(Group.id == match.group_id).first()
        if group and getattr(group, "fine_enabled", False):
            fine_amount = float(getattr(group, "fine_amount", 0) or 0)
            if fine_amount > 0:
                pl = db.query(Player).filter(Player.id == player_id).first()
                uid = (getattr(pl, "owner_id", None) or getattr(pl, "user_id", None)) if pl else None
                if uid:
                    exists = (
                        db.query(GroupFinancialEntry)
                        .filter(GroupFinancialEntry.group_id == match.group_id)
                        .filter(GroupFinancialEntry.user_id == uid)
                        .filter(GroupFinancialEntry.entry_type == "fine")
                        .filter(GroupFinancialEntry.match_id == match.id)
                        .first()
                    )
                    if not exists:
                        entry = GroupFinancialEntry(
                            group_id=match.group_id,
                            user_id=uid,
                            match_id=match.id,
                            entry_type="fine",
                            amount_cents=int(round(fine_amount * 100)),
                            currency=(group.currency or "BRL"),
                            status="pending",
                            due_date=(match.starts_at.date() if match.starts_at else None),
                            description=reason or f"Multa - ausência partida #{match.id}",
                            paid=False,
                            paid_at=None,
                            confirmed_by_user_id=None,
                        )
                        db.add(entry)
                        db.flush()
                        fine_created = True
                        if notification_allowed(db, uid, "fines"):
                            create_notification(
                                db,
                                user_id=uid,
                                type="fine_applied",
                                title="Multa aplicada",
                                message=f"Foi aplicada uma multa na partida #{match.id}.",
                                external_key=f"fine_applied:{match.id}:{uid}",
                                payload={"group_id": match.group_id, "match_id": match.id, "entry_id": entry.id},
                            )
    db.commit()
    if match.group_id:
        rebuild_snapshot(db, group_id=str(match.group_id), reference_month=(match.starts_at or utc_now()).date().replace(day=1))
        db.commit()
    return {"ok": True, "fine_created": fine_created}
