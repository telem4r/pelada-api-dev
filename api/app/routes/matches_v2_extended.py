"""Matches V2 Extended routes — features from frontend antigo not in original V2.

Adds: DELETE match, cancel, close (with charges), no-show, waitlist promote, match join-requests.
"""
from __future__ import annotations

from typing import Any, Optional
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.core.cache import app_cache
from app.core.rate_limit import consume_rate_limit
from app.core.supabase_storage import resolve_avatar_fields
from app.services.notifications_v2_service import NotificationsV2Service
from app.services.matches_v2_service import MatchesV2Service

router = APIRouter(tags=["Matches V2 Extended"])
notifications_service = NotificationsV2Service()
matches_service = MatchesV2Service()


def _create_v2_fine_entry_if_missing(db: Session, *, group_id: str, match_id: str, actor_user_id: str, amount: float, currency: str | None, player_id: str | None = None, guest_id: str | None = None, subject_name: str | None = None) -> bool:
    amount = float(amount or 0)
    if amount <= 0:
        return False

    currency = (currency or 'BRL').strip() or 'BRL'
    subject_name = (subject_name or 'Jogador').strip() or 'Jogador'
    actor_name = (_identity(db, actor_user_id).get('name') or 'Admin').strip() or 'Admin'

    if player_id:
        existing = db.execute(text("""
            select e.id::text
            from public.finance_entries_v2 e
            where e.group_id = cast(:group_id as uuid)
              and e.match_id = cast(:match_id as uuid)
              and e.category = 'fine'
              and e.player_id = cast(:player_id as uuid)
            limit 1
        """), {'group_id': group_id, 'match_id': match_id, 'player_id': player_id}).scalar()
        if existing:
            return False

        notes = f"Multa falta - {subject_name}"
        description = f"Multa falta - {subject_name} • Aplicada por {actor_name}"
        entry_id = db.execute(text("""
            insert into public.finance_entries_v2 (
                id, group_id, player_id, match_id, entry_type, category, amount, currency, paid_at, notes, created_by_user_id, created_at
            ) values (
                gen_random_uuid(), cast(:group_id as uuid), cast(:player_id as uuid), cast(:match_id as uuid),
                'inflow', 'fine', :amount, :currency, now(), :notes, cast(:actor_user_id as uuid), now()
            ) returning id::text
        """), {
            'group_id': group_id,
            'player_id': player_id,
            'match_id': match_id,
            'amount': amount,
            'currency': currency,
            'notes': notes,
            'actor_user_id': actor_user_id,
        }).scalar()
    else:
        guest_key = f'guest_id:{guest_id}'
        existing = db.execute(text("""
            select e.id::text
            from public.finance_entries_v2 e
            where e.group_id = cast(:group_id as uuid)
              and e.match_id = cast(:match_id as uuid)
              and e.category = 'fine'
              and e.notes like :guest_key
            limit 1
        """), {'group_id': group_id, 'match_id': match_id, 'guest_key': f'{guest_key}%'}).scalar()
        if existing:
            return False

        notes = f"{guest_key};Multa falta - {subject_name}"
        description = f"Multa falta - {subject_name} • Aplicada por {actor_name}"
        entry_id = db.execute(text("""
            insert into public.finance_entries_v2 (
                id, group_id, match_id, entry_type, category, amount, currency, paid_at, notes, created_by_user_id, created_at
            ) values (
                gen_random_uuid(), cast(:group_id as uuid), cast(:match_id as uuid),
                'inflow', 'fine', :amount, :currency, now(), :notes, cast(:actor_user_id as uuid), now()
            ) returning id::text
        """), {
            'group_id': group_id,
            'match_id': match_id,
            'amount': amount,
            'currency': currency,
            'notes': notes,
            'actor_user_id': actor_user_id,
        }).scalar()

    db.execute(text("""
        insert into public.finance_ledger_v2 (
            id, group_id, entry_id, movement_type, direction, amount, balance_impact, description, reference_date, created_at
        ) values (
            gen_random_uuid(), cast(:group_id as uuid), cast(:entry_id as uuid),
            'entrada', 'inflow', :amount, :amount, :description, now(), now()
        )
    """), {
        'group_id': group_id,
        'entry_id': entry_id,
        'amount': amount,
        'description': description,
    })
    return True


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _identity(db: Session, user_id: str) -> dict[str, Any]:
    row = db.execute(text("""
        select u.id::text as user_id, p.id::text as player_id,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as name
        from public.users u join public.players p on p.user_id = u.id
        where u.id = cast(:uid as uuid) limit 1
    """), {'uid': user_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Utilizador não encontrado.")
    return dict(row)


def _require_member(db: Session, group_id: str, user_id: str) -> dict[str, Any]:
    row = db.execute(text("""
        select gm.role::text, gm.status::text, gm.player_id::text
        from public.group_members gm
        join public.players p on p.id = gm.player_id
        where gm.group_id = cast(:gid as uuid) and p.user_id = cast(:uid as uuid) limit 1
    """), {'gid': group_id, 'uid': user_id}).mappings().first()
    if not row or row['status'] != 'active':
        raise HTTPException(status_code=403, detail="Não é membro ativo deste grupo.")
    return dict(row)


def _require_admin(db: Session, group_id: str, user_id: str) -> dict[str, Any]:
    m = _require_member(db, group_id, user_id)
    if m['role'] not in ('owner', 'admin'):
        raise HTTPException(status_code=403, detail="Apenas admin/owner.")
    return m


def _match_exists(db: Session, group_id: str, match_id: str) -> dict[str, Any]:
    row = db.execute(text("""
        select id::text, group_id::text, status::text, created_by_user_id::text
        from public.matches_v2
        where id = cast(:mid as uuid) and group_id = cast(:gid as uuid) limit 1
    """), {'mid': match_id, 'gid': group_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Partida não encontrada.")
    return dict(row)


def _resolve_player_id(db: Session, match_id: str, target_id: str) -> str:
    """Resolve target_id para player_id. Aceita participant_id ou player_id."""
    # Tenta primeiro como player_id direto
    row = db.execute(text("""
        select player_id::text from public.match_participants_v2
        where match_id = cast(:mid as uuid) and player_id = cast(:tid as uuid) limit 1
    """), {'mid': match_id, 'tid': target_id}).scalar()
    if row:
        return row
    # Tenta como participant_id
    row = db.execute(text("""
        select player_id::text from public.match_participants_v2
        where match_id = cast(:mid as uuid) and id = cast(:tid as uuid) limit 1
    """), {'mid': match_id, 'tid': target_id}).scalar()
    if row:
        return row
    return target_id


def _generate_paid_finance_entry(db: Session, group_id: str, match_id: str, player_id: str | None, target: str, actor_user_id: str, guest_id: str | None = None) -> None:
    """Gera entrada financeira (inflow) quando pagamento é marcado."""
    group = db.execute(text("""
        select
            group_type::text as group_type,
            coalesce(single_cost, 0) as single_cost,
            coalesce(per_person_cost, 0) as per_person_cost,
            currency
        from public.groups
        where id = cast(:gid as uuid)
    """), {'gid': group_id}).mappings().first()
    if not group:
        return

    group_type = (group['group_type'] or '').strip().lower()
    if group_type == 'avulso':
        amount = float(group['per_person_cost'] or 0)
    else:
        amount = float(group['single_cost'] or 0)

    if amount <= 0:
        return

    currency = group['currency'] or 'BRL'
    if guest_id:
        player_name = db.execute(text("""
            select coalesce(nullif(trim(name),''), 'Convidado') from public.match_guests_v2
            where id = cast(:gid as uuid) limit 1
        """), {'gid': guest_id}).scalar() or 'Convidado'
        exists = db.execute(text("""
            select id from public.finance_entries_v2
            where group_id = cast(:group_id as uuid) and match_id = cast(:match_id as uuid)
              and category = 'single_guest' and (notes = :legacy_notes or notes = :display_notes) limit 1
        """), {'group_id': group_id, 'match_id': match_id, 'legacy_notes': f'guest_id:{guest_id}', 'display_notes': player_name}).scalar()
        if exists:
            return
        entry_id = db.execute(text("""
            INSERT INTO public.finance_entries_v2
                (id, group_id, match_id, entry_type, category, amount, currency, paid_at, notes, created_by_user_id, created_at)
            VALUES (gen_random_uuid(), cast(:group_id as uuid), cast(:match_id as uuid),
                    'inflow', 'single_guest', :amount, :currency, now(), :notes,
                    cast(:actor_user_id as uuid), now())
            returning id::text
        """), {'group_id': group_id, 'match_id': match_id, 'amount': amount, 'currency': currency, 'notes': player_name, 'actor_user_id': actor_user_id}).scalar()
        db.execute(text("""
            INSERT INTO public.finance_ledger_v2
                (id, group_id, entry_id, movement_type, direction, amount, balance_impact, description, reference_date, created_at)
            VALUES (gen_random_uuid(), cast(:group_id as uuid), cast(:entry_id as uuid), 'entrada', 'credit', :amount, :amount, :description, now(), now())
        """), {'group_id': group_id, 'entry_id': entry_id, 'amount': amount, 'description': f'Pagamento convidado - {player_name}'})
        return
    player_name = db.execute(text("""
        select coalesce(
            nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''),
            nullif(trim(p.display_name),''),
            nullif(trim(p.full_name),''),
            nullif(trim(u.name),''),
            'Jogador'
        ) from public.players p
        left join public.users u on u.id = p.user_id
        where p.id = cast(:pid as uuid) limit 1
    """), {'pid': player_id}).scalar() or 'Jogador'
    exists = db.execute(text("""
        select id from public.finance_entries_v2
        where group_id = cast(:gid as uuid) and match_id = cast(:mid as uuid)
          and category = 'match_payment' and player_id = cast(:pid as uuid) limit 1
    """), {'gid': group_id, 'mid': match_id, 'pid': player_id}).scalar()
    if exists:
        return
    entry_id = db.execute(text("""
        INSERT INTO public.finance_entries_v2
            (id, group_id, match_id, player_id, entry_type, category, amount, currency, paid_at, notes, created_by_user_id, created_at)
        VALUES (gen_random_uuid(), cast(:gid as uuid), cast(:mid as uuid), cast(:pid as uuid),
                'inflow', 'match_payment', :amount, :currency, now(), :notes,
                cast(:actor_user_id as uuid), now())
        returning id::text
    """), {
        'gid': group_id, 'mid': match_id, 'pid': player_id,
        'amount': amount, 'currency': currency, 'notes': player_name,
        'actor_user_id': actor_user_id,
    }).scalar()
    db.execute(text("""
        INSERT INTO public.finance_ledger_v2
            (id, group_id, entry_id, movement_type, direction, amount, balance_impact, description, reference_date, created_at)
        VALUES (gen_random_uuid(), cast(:group_id as uuid), cast(:entry_id as uuid), 'entrada', 'credit', :amount, :amount, :description, now(), now())
    """), {'group_id': group_id, 'entry_id': entry_id, 'amount': amount, 'description': f'Pagamento partida - {player_name}'})


def _find_paid_entry_for_target(db: Session, *, group_id: str, match_id: str, target: str, target_id: str) -> dict[str, Any] | None:
    if target == 'guest':
        row = db.execute(text("""
            select e.id::text as entry_id,
                   e.created_by_user_id::text as created_by_user_id
            from public.finance_entries_v2 e
            left join public.match_guests_v2 g
              on g.match_id = e.match_id
             and (
                    e.notes = concat('guest_id:', g.id::text)
                 or e.notes = coalesce(nullif(trim(g.name), ''), 'Convidado')
                 or e.notes like concat('guest_id:', g.id::text, '%')
             )
            where e.group_id = cast(:group_id as uuid)
              and e.match_id = cast(:match_id as uuid)
              and e.category = 'single_guest'
              and g.id = cast(:guest_id as uuid)
            order by coalesce(e.paid_at, e.created_at) desc
            limit 1
        """), {'group_id': group_id, 'match_id': match_id, 'guest_id': target_id}).mappings().first()
        return dict(row) if row else None
    resolved_pid = _resolve_player_id(db, match_id, target_id)
    row = db.execute(text("""
        select e.id::text as entry_id,
               e.created_by_user_id::text as created_by_user_id
        from public.finance_entries_v2 e
        where e.group_id = cast(:group_id as uuid)
          and e.match_id = cast(:match_id as uuid)
          and e.category = 'match_payment'
          and e.player_id = cast(:player_id as uuid)
        order by coalesce(e.paid_at, e.created_at) desc
        limit 1
    """), {'group_id': group_id, 'match_id': match_id, 'player_id': resolved_pid}).mappings().first()
    return dict(row) if row else None


def _group_finance_context(db: Session, group_id: str) -> dict[str, Any] | None:
    row = db.execute(text("""
        select
            lower(coalesce(group_type::text, '')) as group_type,
            coalesce(per_person_cost, 0) as per_person_cost,
            coalesce(single_cost, 0) as single_cost,
            coalesce(currency, 'BRL') as currency
        from public.groups
        where id = cast(:gid as uuid)
        limit 1
    """), {'gid': group_id}).mappings().first()
    return dict(row) if row else None


def _find_match_obligation_for_target(db: Session, *, group_id: str, match_id: str, target: str, target_id: str) -> dict[str, Any] | None:
    if target == 'guest':
        row = db.execute(text("""
            select id::text as obligation_id, status, amount
            from public.finance_obligations_v2
            where group_id = cast(:group_id as uuid)
              and match_id = cast(:match_id as uuid)
              and source_type = 'convidado_partida'
              and description = :description
            order by created_at desc
            limit 1
        """), {'group_id': group_id, 'match_id': match_id, 'description': f'guest_id:{target_id}'}).mappings().first()
        return dict(row) if row else None

    row = db.execute(text("""
        select id::text as obligation_id, status, amount
        from public.finance_obligations_v2
        where group_id = cast(:group_id as uuid)
          and match_id = cast(:match_id as uuid)
          and player_id = cast(:player_id as uuid)
          and source_type = 'avulso_partida'
        order by created_at desc
        limit 1
    """), {'group_id': group_id, 'match_id': match_id, 'player_id': target_id}).mappings().first()
    return dict(row) if row else None


def _ensure_open_match_obligation_for_target(db: Session, *, group_id: str, match_id: str, target: str, target_id: str, actor_user_id: str) -> dict[str, Any] | None:
    finance = _group_finance_context(db, group_id)
    if not finance:
        return None

    group_type = (finance.get('group_type') or '').strip().lower()

    # Só gera obrigação para grupos avulso ou híbrido
    if group_type not in ('avulso', 'hybrid', 'hibrido', 'híbrido'):
        return None

    # Em grupo híbrido, membros mensalistas NÃO geram valor por partida
    if group_type in ('hybrid', 'hibrido', 'híbrido') and target == 'member':
        billing_type = db.execute(text("""
            select gm.billing_type::text
            from public.group_members gm
            join public.players p on p.id = gm.player_id
            where gm.group_id = cast(:group_id as uuid)
              and gm.player_id = cast(:player_id as uuid)
              and gm.status = cast('active' as membership_status_enum)
            limit 1
        """), {'group_id': group_id, 'player_id': target_id}).scalar()
        bt = (billing_type or '').strip().lower()
        if bt in ('monthly', 'mensalista'):
            return None

    amount = float(finance.get('per_person_cost') or 0)
    if amount <= 0:
        return None

    existing = _find_match_obligation_for_target(db, group_id=group_id, match_id=match_id, target=target, target_id=target_id)
    if existing:
        return existing

    currency = finance.get('currency') or 'BRL'
    if target == 'guest':
        guest = db.execute(text("""
            select coalesce(nullif(trim(name), ''), 'Convidado') as guest_name
            from public.match_guests_v2
            where id = cast(:guest_id as uuid) and match_id = cast(:match_id as uuid)
            limit 1
        """), {'guest_id': target_id, 'match_id': match_id}).mappings().first()
        guest_name = (guest or {}).get('guest_name') or 'Convidado'
        obligation_id = db.execute(text("""
            insert into public.finance_obligations_v2 (
                id, group_id, user_id, player_id, match_id, source_type, title, description,
                amount, currency, status, created_by_user_id, created_at, updated_at
            ) values (
                gen_random_uuid(), cast(:group_id as uuid), null, null, cast(:match_id as uuid),
                'convidado_partida', :title, :description, :amount, :currency, 'aberta',
                cast(:actor_user_id as uuid), now(), now()
            ) returning id::text
        """), {
            'group_id': group_id,
            'match_id': match_id,
            'title': f'Partida - convidado {guest_name}',
            'description': f'guest_id:{target_id}',
            'amount': amount,
            'currency': currency,
            'actor_user_id': actor_user_id,
        }).scalar()
        return {'obligation_id': obligation_id, 'status': 'aberta', 'amount': amount}

    identity = db.execute(text("""
        select mp.user_id::text as user_id, mp.player_id::text as player_id,
               coalesce(
                   nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                   nullif(trim(p.display_name), ''),
                   nullif(trim(p.full_name), ''),
                   nullif(trim(u.name), ''),
                   'Jogador'
               ) as player_name
        from public.match_participants_v2 mp
        join public.players p on p.id = mp.player_id
        join public.users u on u.id = mp.user_id
        where mp.match_id = cast(:match_id as uuid)
          and mp.player_id = cast(:player_id as uuid)
        limit 1
    """), {'match_id': match_id, 'player_id': target_id}).mappings().first()
    if not identity:
        return None

    obligation_id = db.execute(text("""
        insert into public.finance_obligations_v2 (
            id, group_id, user_id, player_id, match_id, source_type, title, description,
            amount, currency, status, created_by_user_id, created_at, updated_at
        ) values (
            gen_random_uuid(), cast(:group_id as uuid), cast(:user_id as uuid), cast(:player_id as uuid), cast(:match_id as uuid),
            'avulso_partida', :title, :description, :amount, :currency, 'aberta',
            cast(:actor_user_id as uuid), now(), now()
        ) returning id::text
    """), {
        'group_id': group_id,
        'user_id': identity['user_id'],
        'player_id': identity['player_id'],
        'match_id': match_id,
        'title': 'Partida - cobrança avulso',
        'description': identity['player_name'],
        'amount': amount,
        'currency': currency,
        'actor_user_id': actor_user_id,
    }).scalar()
    return {'obligation_id': obligation_id, 'status': 'aberta', 'amount': amount}


def _delete_open_obligation_for_target(db: Session, *, group_id: str, match_id: str, target: str, target_id: str) -> None:
    """Apaga obrigação financeira pendente (status 'aberta') ao desmarcar chegada.
    Só apaga se NÃO houver entrada de pagamento associada (ou seja, só se ainda está em aberto).
    """
    obligation = _find_match_obligation_for_target(db, group_id=group_id, match_id=match_id, target=target, target_id=target_id)
    if not obligation:
        return
    status = (obligation.get('status') or '').strip().lower()
    if status not in ('aberta', 'pending'):
        return  # Já paga ou parcial — não apagar
    obligation_id = obligation['obligation_id']
    # Verificar se existe alguma entrada de pagamento vinculada
    has_payment = db.execute(text("""
        select exists(
            select 1 from public.finance_entries_v2
            where group_id = cast(:group_id as uuid)
              and obligation_id = cast(:obligation_id as uuid)
              and entry_type = 'inflow'
        )
    """), {'group_id': group_id, 'obligation_id': obligation_id}).scalar()
    if has_payment:
        return  # Tem pagamento vinculado — não apagar
    # Apagar ledger e entries vinculados (se houver outflow/outros)
    db.execute(text("""
        DELETE FROM public.finance_ledger_v2
        WHERE group_id = cast(:group_id as uuid)
          AND obligation_id = cast(:obligation_id as uuid)
    """), {'group_id': group_id, 'obligation_id': obligation_id})
    db.execute(text("""
        DELETE FROM public.finance_entries_v2
        WHERE group_id = cast(:group_id as uuid)
          AND obligation_id = cast(:obligation_id as uuid)
    """), {'group_id': group_id, 'obligation_id': obligation_id})
    # Apagar a obrigação em si
    db.execute(text("""
        DELETE FROM public.finance_obligations_v2
        WHERE id = cast(:obligation_id as uuid)
          AND group_id = cast(:group_id as uuid)
    """), {'group_id': group_id, 'obligation_id': obligation_id})


def _generate_paid_finance_entry_for_obligation(db: Session, *, group_id: str, match_id: str, target: str, target_id: str, actor_user_id: str) -> None:
    obligation = _ensure_open_match_obligation_for_target(
        db, group_id=group_id, match_id=match_id, target=target, target_id=target_id, actor_user_id=actor_user_id
    )
    if not obligation:
        # fallback para o comportamento antigo em grupos não-avulsos ou sem obrigação
        if target == 'guest':
            _generate_paid_finance_entry(db, group_id, match_id, None, target, actor_user_id, guest_id=target_id)
        else:
            _generate_paid_finance_entry(db, group_id, match_id, target_id, target, actor_user_id)
        return

    obligation_id = obligation['obligation_id']
    existing_entry = db.execute(text("""
        select id::text as entry_id
        from public.finance_entries_v2
        where group_id = cast(:group_id as uuid)
          and obligation_id = cast(:obligation_id as uuid)
          and entry_type = 'inflow'
        order by coalesce(paid_at, created_at) desc
        limit 1
    """), {'group_id': group_id, 'obligation_id': obligation_id}).mappings().first()
    if existing_entry:
        db.execute(text("""
            update public.finance_obligations_v2
            set status = 'paga', updated_at = now()
            where id = cast(:obligation_id as uuid)
        """), {'obligation_id': obligation_id})
        return

    finance = _group_finance_context(db, group_id) or {}
    amount = float(obligation.get('amount') or 0)
    if amount <= 0:
        return
    currency = finance.get('currency') or 'BRL'

    if target == 'guest':
        guest_name = db.execute(text("""
            select coalesce(nullif(trim(name), ''), 'Convidado')
            from public.match_guests_v2
            where id = cast(:guest_id as uuid)
            limit 1
        """), {'guest_id': target_id}).scalar() or 'Convidado'
        notes = f'guest_id:{target_id};{guest_name}'
        category = 'single_guest'
        player_id = None
        user_id = None
        description = f'Pagamento partida - {guest_name}'
    else:
        notes = None
        category = 'match_payment'
        player_id = target_id
        user_id = db.execute(text("""
            select user_id::text from public.match_participants_v2
            where match_id = cast(:match_id as uuid) and player_id = cast(:player_id as uuid)
            limit 1
        """), {'match_id': match_id, 'player_id': target_id}).scalar()
        player_name = db.execute(text("""
            select coalesce(
                nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                nullif(trim(p.display_name), ''),
                nullif(trim(p.full_name), ''),
                nullif(trim(u.name), ''),
                'Jogador'
            )
            from public.players p
            join public.users u on u.id = p.user_id
            where p.id = cast(:player_id as uuid)
            limit 1
        """), {'player_id': target_id}).scalar() or 'Jogador'
        description = f'Pagamento partida - {player_name}'

    entry_id = db.execute(text("""
        insert into public.finance_entries_v2 (
            id, group_id, obligation_id, user_id, player_id, match_id, entry_type, category,
            amount, currency, paid_at, notes, created_by_user_id, created_at
        ) values (
            gen_random_uuid(), cast(:group_id as uuid), cast(:obligation_id as uuid),
            case when :user_id is not null then cast(:user_id as uuid) end,
            case when :player_id is not null then cast(:player_id as uuid) end,
            cast(:match_id as uuid), 'inflow', :category, :amount, :currency, now(), :notes,
            cast(:actor_user_id as uuid), now()
        ) returning id::text
    """), {
        'group_id': group_id,
        'obligation_id': obligation_id,
        'user_id': user_id,
        'player_id': player_id,
        'match_id': match_id,
        'category': category,
        'amount': amount,
        'currency': currency,
        'notes': notes,
        'actor_user_id': actor_user_id,
    }).scalar()

    db.execute(text("""
        insert into public.finance_ledger_v2 (
            id, group_id, obligation_id, entry_id, movement_type, direction, amount, balance_impact, description, reference_date, created_at
        ) values (
            gen_random_uuid(), cast(:group_id as uuid), cast(:obligation_id as uuid), cast(:entry_id as uuid),
            'entrada', 'credit', :amount, :amount, :description, now(), now()
        )
    """), {
        'group_id': group_id,
        'obligation_id': obligation_id,
        'entry_id': entry_id,
        'amount': amount,
        'description': description,
    })

    db.execute(text("""
        update public.finance_obligations_v2
        set status = 'paga', updated_at = now()
        where id = cast(:obligation_id as uuid)
    """), {'obligation_id': obligation_id})


# ═══════════════════════════════════════════════════════════════════════
# DELETE MATCH
# ═══════════════════════════════════════════════════════════════════════

@router.delete("/v2/groups/{group_id}/matches/{match_id}", status_code=204)
def delete_match(group_id: str, match_id: str,
                 principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                 db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    match = _match_exists(db, group_id, match_id)
    if match['status'] in ('in_progress', 'finished'):
        raise HTTPException(status_code=400, detail="Não é possível eliminar partida em andamento ou finalizada.")
    # Cascade deletes participants, guests, events, stats, draws
    db.execute(text("DELETE FROM public.matches_v2 WHERE id = cast(:mid as uuid)"), {'mid': match_id})
    db.commit()
    app_cache.invalidate_prefix(f"matches_v2:group:{group_id}")


# ═══════════════════════════════════════════════════════════════════════
# CANCEL MATCH
# ═══════════════════════════════════════════════════════════════════════

@router.post("/v2/groups/{group_id}/matches/{match_id}/cancel")
def cancel_match(group_id: str, match_id: str,
                 principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                 db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    match = _match_exists(db, group_id, match_id)
    if match['status'] == 'finished':
        raise HTTPException(status_code=400, detail="Partida já finalizada.")
    db.execute(text("""
        UPDATE public.matches_v2 SET status = 'cancelled', updated_at = now()
        WHERE id = cast(:mid as uuid)
    """), {'mid': match_id})
    db.commit()
    app_cache.invalidate_prefix(f"matches_v2:group:{group_id}")
    return {"status": "cancelled", "match_id": match_id}


# ═══════════════════════════════════════════════════════════════════════
# CLOSE MATCH (finish + generate financial charges)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/v2/groups/{group_id}/matches/{match_id}/close")
def close_match(group_id: str, match_id: str,
                payload: dict = Body(default={}),
                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    match = _match_exists(db, group_id, match_id)
    generate_charges = payload.get('generate_charges', True)
    generate_venue = payload.get('generate_venue', True)

    # Finish the match
    db.execute(text("""
        UPDATE public.matches_v2 SET status = 'finished', finished_at = now(), updated_at = now()
        WHERE id = cast(:mid as uuid) AND status != 'finished'
    """), {'mid': match_id})

    if generate_charges:
        # Get group finance context
        group = db.execute(text("""
            select coalesce(single_cost, 0) as single_cost, coalesce(venue_cost, 0) as venue_cost,
                   currency, lower(coalesce(group_type::text, '')) as group_type
            from public.groups where id = cast(:gid as uuid)
        """), {'gid': group_id}).mappings().first()

        close_gt = (group['group_type'] if group else '') or ''

        if group and float(group['single_cost'] or 0) > 0 and close_gt in ('avulso', 'hybrid', 'hibrido', 'híbrido'):
            # Gera obrigação apenas para participantes que:
            # 1) chegaram (has_arrived = true)
            # 2) NÃO estão marcados como pago (is_paid = false)
            # 3) Em grupo híbrido: NÃO são mensalistas
            participants = db.execute(text("""
                select p.id::text as participant_id, p.user_id::text, p.player_id::text, p.kind
                from public.match_participants_v2 p
                left join public.group_members gm
                  on gm.player_id = p.player_id
                 and gm.group_id = cast(:gid as uuid)
                 and gm.status = cast('active' as membership_status_enum)
                where p.match_id = cast(:mid as uuid)
                  and p.status = 'confirmado'
                  and p.has_arrived = true
                  and coalesce(p.is_paid, false) = false
                  and (
                      :group_type = 'avulso'
                      or coalesce(lower(gm.billing_type::text), 'avulso') not in ('monthly', 'mensalista')
                  )
            """), {'mid': match_id, 'gid': group_id, 'group_type': close_gt}).mappings().all()

            for part in participants:
                # Usa _ensure_open para não duplicar obrigações já criadas pelo admin_mark
                _ensure_open_match_obligation_for_target(
                    db, group_id=group_id, match_id=match_id,
                    target='member', target_id=part['player_id'],
                    actor_user_id=principal.user_id,
                )

        if generate_venue and group and float(group['venue_cost'] or 0) > 0:
            db.execute(text("""
                INSERT INTO public.finance_entries_v2
                    (id, group_id, match_id, entry_type, category, amount, currency, created_at)
                VALUES (gen_random_uuid(), cast(:gid as uuid), cast(:mid as uuid),
                        'outflow', 'venue', :amount, :currency, now())
            """), {
                'gid': group_id, 'mid': match_id,
                'amount': float(group['venue_cost']), 'currency': group['currency'] or 'BRL',
            })

    db.commit()
    matches_service._invalidate_group_cache(group_id=group_id, match_id=match_id)
    app_cache.invalidate_prefix(f'ranking_v2:group:{group_id}')
    return {"status": "closed", "match_id": match_id, "charges_generated": generate_charges}



@router.delete("/v2/groups/{group_id}/matches/{match_id}/participants/{player_id}")
def remove_participant(group_id: str, match_id: str, player_id: str,
                       principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                       db: Session = Depends(get_db_session)):
    resolved_pid = _resolve_player_id(db, match_id, player_id)
    matches_service.remove_member_presence_as_admin(db, principal, group_id, match_id, resolved_pid)
    return {"status": "removed", "player_id": resolved_pid}


# ═══════════════════════════════════════════════════════════════════════
# NO-SHOW (member & guest)
# ═══════════════════════════════════════════════════════════════════════

class NoShowPayload(BaseModel):
    justified: bool = False
    reason: Optional[str] = None
    apply_fine: bool = False


@router.post("/v2/groups/{group_id}/matches/{match_id}/participants/{player_id}/no-show")
def mark_no_show_member(group_id: str, match_id: str, player_id: str,
                        payload: NoShowPayload = NoShowPayload(),
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    _match_exists(db, group_id, match_id)
    resolved_pid = _resolve_player_id(db, match_id, player_id)
    result = db.execute(text("""
        UPDATE public.match_participants_v2
        SET no_show = true, no_show_justified = :justified, no_show_reason = :reason
        WHERE match_id = cast(:mid as uuid) AND player_id = cast(:pid as uuid)
    """), {'mid': match_id, 'pid': resolved_pid, 'justified': payload.justified, 'reason': payload.reason})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Participante não encontrado nesta partida.")

    fine_applied = False
    if payload.apply_fine and not payload.justified:
        group = db.execute(text("""
            select fine_enabled, coalesce(fine_amount, 0) as fine_amount, currency
            from public.groups where id = cast(:gid as uuid)
        """), {'gid': group_id}).mappings().first()
        if group and group['fine_enabled'] and float(group['fine_amount'] or 0) > 0:
            player_name = db.execute(text("""
                select coalesce(
                    nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''),
                    nullif(trim(p.display_name),''),
                    nullif(trim(p.full_name),''),
                    nullif(trim(u.name),''),
                    'Jogador'
                ) from public.players p
                left join public.users u on u.id = p.user_id
                where p.id = cast(:pid as uuid) limit 1
            """), {'pid': resolved_pid}).scalar() or 'Jogador'
            fine_applied = _create_v2_fine_entry_if_missing(
                db,
                group_id=group_id,
                match_id=match_id,
                actor_user_id=principal.user_id,
                amount=float(group['fine_amount']),
                currency=group['currency'] or 'BRL',
                player_id=resolved_pid,
                subject_name=player_name,
            )

    db.commit()
    app_cache.invalidate_prefix(f"finance_v2:summary:group:{group_id}")
    app_cache.invalidate_prefix(f"finance_v2:entries:group:{group_id}")
    app_cache.invalidate_prefix(f"finance_v2:ledger:group:{group_id}")
    return {"status": "no_show", "player_id": resolved_pid, "justified": payload.justified, "fine_applied": fine_applied}

@router.post("/v2/groups/{group_id}/matches/{match_id}/guests/{guest_id}/no-show")
def mark_no_show_guest(group_id: str, match_id: str, guest_id: str,
                       payload: NoShowPayload = NoShowPayload(),
                       principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                       db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    _match_exists(db, group_id, match_id)
    result = db.execute(text("""
        UPDATE public.match_guests_v2
        SET no_show = true, no_show_justified = :justified, no_show_reason = :reason
        WHERE id = cast(:gid as uuid) AND match_id = cast(:mid as uuid)
    """), {'mid': match_id, 'gid': guest_id, 'justified': payload.justified, 'reason': payload.reason})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Convidado não encontrado nesta partida.")

    fine_applied = False
    if payload.apply_fine and not payload.justified:
        group = db.execute(text("""
            select fine_enabled, coalesce(fine_amount, 0) as fine_amount, currency
            from public.groups where id = cast(:gid as uuid)
        """), {'gid': group_id}).mappings().first()
        if group and group['fine_enabled'] and float(group['fine_amount'] or 0) > 0:
            guest_name = db.execute(text("""
                select coalesce(nullif(trim(name), ''), 'Convidado')
                from public.match_guests_v2
                where id = cast(:guest_id as uuid) and match_id = cast(:match_id as uuid)
                limit 1
            """), {'guest_id': guest_id, 'match_id': match_id}).scalar() or 'Convidado'
            fine_applied = _create_v2_fine_entry_if_missing(
                db,
                group_id=group_id,
                match_id=match_id,
                actor_user_id=principal.user_id,
                amount=float(group['fine_amount']),
                currency=group['currency'] or 'BRL',
                guest_id=guest_id,
                subject_name=guest_name,
            )

    db.commit()
    app_cache.invalidate_prefix(f"finance_v2:summary:group:{group_id}")
    app_cache.invalidate_prefix(f"finance_v2:entries:group:{group_id}")
    app_cache.invalidate_prefix(f"finance_v2:ledger:group:{group_id}")
    return {"status": "no_show", "guest_id": guest_id, "justified": payload.justified, "fine_applied": fine_applied}

# ═══════════════════════════════════════════════════════════════════════
# WAITLIST PROMOTE
# ═══════════════════════════════════════════════════════════════════════

@router.post("/v2/groups/{group_id}/matches/{match_id}/waitlist/promote")
def promote_waitlist(group_id: str, match_id: str,
                     payload: dict = Body(default={}),
                     principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                     db: Session = Depends(get_db_session)):
    count = int(payload.get('count', 1))
    promoted = matches_service.promote_waitlist(db, principal, group_id, match_id, count)
    return {"promoted": promoted, "match_id": match_id}


# ═══════════════════════════════════════════════════════════════════════
# APPROVE MEMBER PRESENCE (admin mark as confirmed)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/v2/groups/{group_id}/matches/{match_id}/approve-member")
def approve_member_presence(group_id: str, match_id: str,
                            payload: dict = Body(...),
                            principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                            db: Session = Depends(get_db_session)):
    player_id = payload.get('player_id')
    position = (payload.get('position') or 'linha')
    if not player_id:
        raise HTTPException(status_code=400, detail="player_id é obrigatório.")
    matches_service.approve_member_presence(db, principal, group_id, match_id, str(player_id), str(position))
    return {"status": "approved", "player_id": str(player_id), "approved_by_user_id": principal.user_id}


@router.post("/v2/groups/{group_id}/matches/{match_id}/unapprove-member")
def unapprove_member_presence(group_id: str, match_id: str,
                              payload: dict = Body(...),
                              principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    player_id = payload.get('player_id')
    if not player_id:
        raise HTTPException(status_code=400, detail="player_id é obrigatório.")
    matches_service.unapprove_member_presence(db, principal, group_id, match_id, str(player_id))
    return {"status": "unapproved", "player_id": str(player_id)}


# ═══════════════════════════════════════════════════════════════════════
# ADMIN MARK (arrival/paid - legacy compatibility)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/v2/groups/{group_id}/matches/{match_id}/admin/mark")
def admin_mark(group_id: str, match_id: str,
               payload: dict = Body(...),
               principal: SupabasePrincipal = Depends(get_current_supabase_principal),
               db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    match = _match_exists(db, group_id, match_id)
    if match.get('status') == 'finished' and payload.get('arrived') is not None:
        raise HTTPException(status_code=409, detail='A partida já foi finalizada. Não é possível alterar o status de chegada.')
    target = payload.get('target', 'member')
    target_id = payload.get('target_id', '')
    arrived = payload.get('arrived')
    paid = payload.get('paid')
    finance = _group_finance_context(db, group_id) or {}
    _gt = (finance.get('group_type') or '').strip().lower()
    should_generate_obligation = _gt in ('avulso', 'hybrid', 'hibrido', 'híbrido')

    if target == 'guest':
        updates = []
        params: dict[str, Any] = {'gid': str(target_id), 'mid': match_id}
        if arrived is not None:
            updates.append("has_arrived = :arrived")
            params['arrived'] = arrived
        if arrived is True:
            updates.append("arrival_marked_by_user_id = cast(:arrival_actor as uuid)")
            params['arrival_actor'] = principal.user_id
        if arrived is False:
            # Trava de segurança: só quem marcou a chegada pode desmarcar
            arrival_marker = db.execute(text("""
                select arrival_marked_by_user_id::text from public.match_guests_v2
                where id = cast(:gid as uuid) and match_id = cast(:mid as uuid) limit 1
            """), {'gid': str(target_id), 'mid': match_id}).scalar()
            if arrival_marker and arrival_marker != principal.user_id:
                raise HTTPException(status_code=403, detail='Somente quem marcou a chegada pode desmarcar.')
            updates.append("arrival_marked_by_user_id = null")
        if paid is not None:
            updates.append("is_paid = :paid")
            params['paid'] = paid
        if paid is False:
            entry = _find_paid_entry_for_target(db, group_id=group_id, match_id=match_id, target='guest', target_id=str(target_id))
            if entry and entry.get('created_by_user_id') and entry.get('created_by_user_id') != principal.user_id:
                raise HTTPException(status_code=403, detail='Somente quem marcou o pagamento pode desfazer.')
            if entry:
                obligation_id = db.execute(text("""
                    select obligation_id::text from public.finance_entries_v2
                    where group_id = cast(:group_id as uuid)
                      and id = cast(:entry_id as uuid)
                    limit 1
                """), {'group_id': group_id, 'entry_id': entry['entry_id']}).scalar()
                db.execute(text("""
                    DELETE FROM public.finance_ledger_v2
                    WHERE group_id = cast(:group_id as uuid)
                      AND entry_id = cast(:entry_id as uuid)
                """), {'group_id': group_id, 'entry_id': entry['entry_id']})
                db.execute(text("""
                    DELETE FROM public.finance_entries_v2
                    WHERE group_id = cast(:group_id as uuid)
                      AND id = cast(:entry_id as uuid)
                """), {'group_id': group_id, 'entry_id': entry['entry_id']})
                if obligation_id:
                    db.execute(text("""
                        update public.finance_obligations_v2
                        set status = 'aberta', updated_at = now()
                        where id = cast(:obligation_id as uuid)
                    """), {'obligation_id': obligation_id})
        if updates:
            db.execute(text(f"""
                UPDATE public.match_guests_v2 SET {', '.join(updates)}, updated_at = now()
                WHERE id = cast(:gid as uuid) AND match_id = cast(:mid as uuid)
            """), params)
        if arrived is True and should_generate_obligation:
            # Só gera obrigação se o convidado NÃO está marcado como pago
            guest_paid = db.execute(text("""
                select coalesce(is_paid, false) from public.match_guests_v2
                where id = cast(:gid as uuid) and match_id = cast(:mid as uuid) limit 1
            """), {'gid': str(target_id), 'mid': match_id}).scalar()
            if not guest_paid:
                _ensure_open_match_obligation_for_target(db, group_id=group_id, match_id=match_id, target='guest', target_id=str(target_id), actor_user_id=principal.user_id)
        if arrived is False and should_generate_obligation:
            # Ao desmarcar chegada, apagar obrigação financeira pendente (se aberta)
            _delete_open_obligation_for_target(db, group_id=group_id, match_id=match_id, target='guest', target_id=str(target_id))
        if paid is True:
            if should_generate_obligation:
                _generate_paid_finance_entry_for_obligation(db, group_id=group_id, match_id=match_id, target='guest', target_id=str(target_id), actor_user_id=principal.user_id)
            else:
                _generate_paid_finance_entry(db, group_id, match_id, None, target, principal.user_id, guest_id=str(target_id))
    else:
        resolved_pid = _resolve_player_id(db, match_id, str(target_id))
        updates = []
        params = {'pid': resolved_pid, 'mid': match_id}
        if arrived is not None:
            updates.append("has_arrived = :arrived")
            params['arrived'] = arrived
        if arrived is True:
            updates.append("arrival_marked_by_user_id = cast(:arrival_actor as uuid)")
            params['arrival_actor'] = principal.user_id
        if arrived is False:
            # Trava de segurança: só quem marcou a chegada pode desmarcar
            arrival_marker = db.execute(text("""
                select arrival_marked_by_user_id::text from public.match_participants_v2
                where player_id = cast(:pid as uuid) and match_id = cast(:mid as uuid) limit 1
            """), {'pid': resolved_pid, 'mid': match_id}).scalar()
            if arrival_marker and arrival_marker != principal.user_id:
                raise HTTPException(status_code=403, detail='Somente quem marcou a chegada pode desmarcar.')
            updates.append("arrival_marked_by_user_id = null")
        if paid is not None:
            updates.append("is_paid = :paid")
            params['paid'] = paid
        if paid is False:
            entry = _find_paid_entry_for_target(db, group_id=group_id, match_id=match_id, target='member', target_id=resolved_pid)
            if entry and entry.get('created_by_user_id') and entry.get('created_by_user_id') != principal.user_id:
                raise HTTPException(status_code=403, detail='Somente quem marcou o pagamento pode desfazer.')
            if entry:
                obligation_id = db.execute(text("""
                    select obligation_id::text from public.finance_entries_v2
                    where group_id = cast(:group_id as uuid)
                      and id = cast(:entry_id as uuid)
                    limit 1
                """), {'group_id': group_id, 'entry_id': entry['entry_id']}).scalar()
                db.execute(text("""
                    DELETE FROM public.finance_ledger_v2
                    WHERE group_id = cast(:group_id as uuid)
                      AND entry_id = cast(:entry_id as uuid)
                """), {'group_id': group_id, 'entry_id': entry['entry_id']})
                db.execute(text("""
                    DELETE FROM public.finance_entries_v2
                    WHERE group_id = cast(:group_id as uuid)
                      AND id = cast(:entry_id as uuid)
                """), {'group_id': group_id, 'entry_id': entry['entry_id']})
                if obligation_id:
                    db.execute(text("""
                        update public.finance_obligations_v2
                        set status = 'aberta', updated_at = now()
                        where id = cast(:obligation_id as uuid)
                    """), {'obligation_id': obligation_id})
        if updates:
            db.execute(text(f"""
                UPDATE public.match_participants_v2 SET {', '.join(updates)}, updated_at = now()
                WHERE player_id = cast(:pid as uuid) AND match_id = cast(:mid as uuid)
            """), params)
        if arrived is True and should_generate_obligation:
            # Só gera obrigação se o membro NÃO está marcado como pago
            member_paid = db.execute(text("""
                select coalesce(is_paid, false) from public.match_participants_v2
                where player_id = cast(:pid as uuid) and match_id = cast(:mid as uuid) limit 1
            """), {'pid': resolved_pid, 'mid': match_id}).scalar()
            if not member_paid:
                _ensure_open_match_obligation_for_target(db, group_id=group_id, match_id=match_id, target='member', target_id=resolved_pid, actor_user_id=principal.user_id)
        if arrived is False and should_generate_obligation:
            # Ao desmarcar chegada, apagar obrigação financeira pendente (se aberta)
            _delete_open_obligation_for_target(db, group_id=group_id, match_id=match_id, target='member', target_id=resolved_pid)
        if paid is True:
            if should_generate_obligation:
                _generate_paid_finance_entry_for_obligation(db, group_id=group_id, match_id=match_id, target='member', target_id=resolved_pid, actor_user_id=principal.user_id)
            else:
                _generate_paid_finance_entry(db, group_id, match_id, resolved_pid, target, principal.user_id)

    db.commit()
    app_cache.invalidate_prefix(f"finance_v2:summary:group:{group_id}")
    app_cache.invalidate_prefix(f"finance_v2:entries:group:{group_id}")
    app_cache.invalidate_prefix(f"finance_v2:ledger:group:{group_id}")
    app_cache.invalidate_prefix(f"matches_v2:group:{group_id}")
    return {"status": "updated", "target": target, "target_id": str(target_id)}


# ═══════════════════════════════════════════════════════════════════════
# MATCH JOIN REQUESTS
# ═══════════════════════════════════════════════════════════════════════

@router.post("/v2/groups/{group_id}/matches/{match_id}/join-requests", status_code=201)
def create_match_join_request(group_id: str, match_id: str,
                              principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    identity = _identity(db, principal.user_id)
    _match_exists(db, group_id, match_id)
    row = db.execute(text("""
        INSERT INTO public.match_join_requests_v2 (match_id, group_id, requester_user_id, requester_player_id)
        VALUES (cast(:mid as uuid), cast(:gid as uuid), cast(:uid as uuid), cast(:pid as uuid))
        ON CONFLICT (match_id, requester_user_id) DO UPDATE SET status = 'pending'
        RETURNING id::text, match_id::text, requester_user_id::text, status, created_at
    """), {'mid': match_id, 'gid': group_id, 'uid': principal.user_id,
           'pid': identity['player_id']}).mappings().first()
    db.commit()
    result = dict(row)
    result['requester_name'] = identity['name']
    return result


@router.get("/v2/groups/{group_id}/matches/{match_id}/join-requests")
def list_match_join_requests(group_id: str, match_id: str,
                             principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                             db: Session = Depends(get_db_session)):
    _require_member(db, group_id, principal.user_id)
    rows = db.execute(text("""
        SELECT jr.id::text, jr.match_id::text, jr.requester_user_id::text, jr.status, jr.created_at,
               coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as requester_name
        FROM public.match_join_requests_v2 jr
        LEFT JOIN public.players p ON p.id = jr.requester_player_id
        LEFT JOIN public.users u ON u.id = jr.requester_user_id
        WHERE jr.match_id = cast(:mid as uuid) AND jr.group_id = cast(:gid as uuid)
        ORDER BY jr.created_at ASC
    """), {'mid': match_id, 'gid': group_id}).mappings().all()
    payload = [resolve_avatar_fields(dict(r)) for r in rows]
    return payload


@router.post("/v2/groups/{group_id}/matches/{match_id}/join-requests/{request_id}/approve")
def approve_match_join_request(group_id: str, match_id: str, request_id: str,
                               principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                               db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    jr = db.execute(text("""
        SELECT id::text, requester_user_id::text, requester_player_id::text, status
        FROM public.match_join_requests_v2
        WHERE id = cast(:rid as uuid) AND match_id = cast(:mid as uuid)
    """), {'rid': request_id, 'mid': match_id}).mappings().first()
    if not jr:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada.")
    if jr['status'] != 'pending':
        raise HTTPException(status_code=400, detail="Solicitação já processada.")

    # Approve: update request + add participant
    db.execute(text("UPDATE public.match_join_requests_v2 SET status = 'approved' WHERE id = cast(:rid as uuid)"),
               {'rid': request_id})
    if jr['requester_player_id']:
        db.execute(text("""
            INSERT INTO public.match_participants_v2
                (id, match_id, user_id, player_id, kind, position, status, queue_order, created_at, updated_at)
            VALUES (gen_random_uuid(), cast(:mid as uuid), cast(:uid as uuid), cast(:pid as uuid),
                    'member', 'linha', 'confirmado', 0, now(), now())
            ON CONFLICT (match_id, player_id) DO UPDATE SET status = 'confirmado'
        """), {'mid': match_id, 'uid': jr['requester_user_id'], 'pid': jr['requester_player_id']})
    db.commit()
    return {"status": "approved", "request_id": request_id}


@router.post("/v2/groups/{group_id}/matches/{match_id}/join-requests/{request_id}/reject")
def reject_match_join_request(group_id: str, match_id: str, request_id: str,
                              principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                              db: Session = Depends(get_db_session)):
    _require_admin(db, group_id, principal.user_id)
    result = db.execute(text("""
        UPDATE public.match_join_requests_v2 SET status = 'rejected'
        WHERE id = cast(:rid as uuid) AND match_id = cast(:mid as uuid) AND status = 'pending'
    """), {'rid': request_id, 'mid': match_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada ou já processada.")
    db.commit()
    return {"status": "rejected", "request_id": request_id}


# ═══════════════════════════════════════════════════════════════════════
# DELETE GROUP
# ═══════════════════════════════════════════════════════════════════════

@router.delete("/v2/groups/{group_id}", status_code=204)
def delete_group(group_id: str,
                 principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                 db: Session = Depends(get_db_session)):
    # Only owner can delete
    row = db.execute(text("""
        SELECT owner_user_id::text FROM public.groups WHERE id = cast(:gid as uuid)
    """), {'gid': group_id}).scalar()
    if not row or row != principal.user_id:
        raise HTTPException(status_code=403, detail="Apenas o dono do grupo pode eliminá-lo.")
    # Cascade deletes members, matches, finance, etc
    db.execute(text("DELETE FROM public.groups WHERE id = cast(:gid as uuid)"), {'gid': group_id})
    db.commit()


# ═══════════════════════════════════════════════════════════════════════
# GROUP SEARCH (public groups)
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/search")
def search_groups(q: str = "",
                  request: Request = None,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    if request is not None:
        consume_rate_limit(request, scope="groups:search", limit=60, window_seconds=60)
    _identity(db, principal.user_id)
    query = q.strip().lower()
    if not query:
        return []

    cache_key = f"groups-search:{principal.user_id}:{query}"
    cached = app_cache.get(cache_key)
    if cached is not None:
        return cached

    rows = db.execute(text("""
        SELECT
            g.id::text,
            g.name,
            g.description,
            coalesce(g.currency, 'BRL') as currency,
            g.group_type::text,
            g.owner_user_id::text,
            g.city,
            g.state,
            g.country,
            g.modality,
            coalesce(nullif(trim(concat_ws(' ', nullif(trim(owner_user.first_name),''), nullif(trim(owner_user.last_name),''))), ''), nullif(trim(owner_player.display_name),''), owner_user.email, '') as owner_name,
            coalesce(avatar.avatar_url, owner_player.avatar_url) as avatar_url,
            (
                SELECT count(*)::int
                FROM public.group_members gm
                WHERE gm.group_id = g.id AND gm.status = 'active'
            ) as members_count,
            (g.owner_user_id = cast(:uid as uuid)) as is_owner,
            exists(
                SELECT 1
                FROM public.group_members gm_admin
                WHERE gm_admin.group_id = g.id
                  AND gm_admin.user_id = cast(:uid as uuid)
                  AND gm_admin.status = 'active'
                  AND lower(coalesce(gm_admin.role::text, '')) = 'admin'
            ) as is_admin,
            CASE
                WHEN exists(
                    SELECT 1
                    FROM public.group_members gm_member
                    WHERE gm_member.group_id = g.id
                      AND gm_member.user_id = cast(:uid as uuid)
                      AND gm_member.status = 'active'
                ) THEN 'member'
                WHEN exists(
                    SELECT 1
                    FROM public.group_join_requests gjr
                    WHERE gjr.group_id = g.id
                      AND gjr.user_id = cast(:uid as uuid)
                      AND lower(coalesce(gjr.status::text, '')) = 'pending'
                ) THEN 'pending'
                ELSE 'none'
            END as join_request_status
        FROM public.groups g
        LEFT JOIN public.users owner_user ON owner_user.id = g.owner_user_id
        LEFT JOIN public.players owner_player ON owner_player.user_id = g.owner_user_id
        LEFT JOIN public.group_avatars avatar ON avatar.group_id = g.id
        WHERE g.is_active = true
          AND coalesce(g.is_public, false) = true
          AND lower(g.name) LIKE :q
        ORDER BY g.name ASC
        LIMIT 20
    """), {'uid': principal.user_id, 'q': f"%{query}%"}).mappings().all()
    return [resolve_avatar_fields(dict(r)) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# FINANCE: DEBTORS, REPORTS, PLAYER STATUS
# ═══════════════════════════════════════════════════════════════════════

@router.get("/v2/groups/{group_id}/finance/debtors")
def list_debtors(group_id: str,
                 principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                 db: Session = Depends(get_db_session)):
    _require_member(db, group_id, principal.user_id)
    rows = db.execute(text("""
        SELECT o.user_id::text, o.player_id::text,
               coalesce(
                   nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''),
                   nullif(trim(p.display_name),''),
                   nullif(trim(p.full_name),''),
                   nullif(trim(u.name),''),
                   'Jogador'
               ) as user_name,
               coalesce(nullif(trim(p.avatar_url),''), nullif(trim(u.avatar_url),'')) as user_avatar_url,
               sum(o.amount)::float as pending_amount,
               0.0 as overdue_amount,
               count(*)::int as charges_count
        FROM public.finance_obligations_v2 o
        LEFT JOIN public.players p ON p.id = o.player_id
        LEFT JOIN public.users u ON u.id = p.user_id
        WHERE o.group_id = cast(:gid as uuid) AND o.status = 'pending'
        GROUP BY o.user_id, o.player_id, p.display_name, p.full_name, p.avatar_url, u.first_name, u.last_name, u.name, u.avatar_url
        HAVING sum(o.amount) > 0
        ORDER BY pending_amount DESC
    """), {'gid': group_id}).mappings().all()
    return [resolve_avatar_fields(dict(r)) for r in rows]


@router.get("/v2/groups/{group_id}/finance/reports")
def get_finance_reports(group_id: str,
                        principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                        db: Session = Depends(get_db_session)):
    _require_member(db, group_id, principal.user_id)
    row = db.execute(text("""
        SELECT
            coalesce(sum(case when entry_type = 'inflow' then amount else 0 end), 0)::float as total_received,
            coalesce(sum(case when entry_type = 'outflow' then amount else 0 end), 0)::float as total_expenses,
            coalesce(sum(case when entry_type = 'inflow' then amount else -amount end), 0)::float as group_balance
        FROM public.finance_entries_v2
        WHERE group_id = cast(:gid as uuid)
    """), {'gid': group_id}).mappings().first()
    obligations = db.execute(text("""
        SELECT coalesce(sum(amount), 0)::float as total_pending
        FROM public.finance_obligations_v2
        WHERE group_id = cast(:gid as uuid) AND status = 'pending'
    """), {'gid': group_id}).scalar() or 0

    return {
        "group_id": group_id, "currency": "BRL",
        "total_to_receive": obligations,
        "total_received": row['total_received'] if row else 0,
        "total_pending": obligations,
        "fines_generated": 0, "match_revenue": 0,
        "group_balance": row['group_balance'] if row else 0,
        "by_type": {}, "received": {}, "expenses": {}, "snapshot": {},
    }


@router.get("/v2/groups/{group_id}/finance/player-status")
def get_player_financial_status(group_id: str,
                                principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                                db: Session = Depends(get_db_session)):
    _require_member(db, group_id, principal.user_id)
    rows = db.execute(text("""
        SELECT gm.user_id::text, gm.player_id::text,
               coalesce(
                   nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''),
                   nullif(trim(p.display_name),''),
                   nullif(trim(p.full_name),''),
                   nullif(trim(u.name),''),
                   'Jogador'
               ) as player_name,
               coalesce(gm.billing_type::text, 'single') as billing_type,
               coalesce((SELECT sum(amount) FROM public.finance_obligations_v2
                         WHERE group_id = cast(:gid as uuid) AND player_id = gm.player_id AND status = 'pending'), 0)::float as pending_total,
               false as monthly_due,
               'adimplente' as financial_status
        FROM public.group_members gm
        JOIN public.players p ON p.id = gm.player_id
        JOIN public.users u ON u.id = p.user_id
        WHERE gm.group_id = cast(:gid as uuid) AND gm.status = 'active'
    """), {'gid': group_id}).mappings().all()
    return {"items": [resolve_avatar_fields(dict(r)) for r in rows]}


@router.get("/v2/groups/{group_id}/finance/me")
def get_my_wallet(group_id: str,
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),
                  db: Session = Depends(get_db_session)):
    identity = _identity(db, principal.user_id)
    member = _require_member(db, group_id, principal.user_id)

    obligations = db.execute(text("""
        select o.id::text as obligation_id,
               o.group_id::text as group_id,
               o.user_id::text as user_id,
               o.player_id::text as player_id,
               o.match_id::text as match_id,
               coalesce(
                   nullif(trim(concat_ws(' ', nullif(trim(pu.first_name),''), nullif(trim(pu.last_name),''))), ''),
                   nullif(trim(p.display_name), ''),
                   nullif(trim(p.full_name), ''),
                   nullif(trim(pu.name), ''),
                   'Jogador'
               ) as player_name,
               o.source_type,
               o.title,
               o.description,
               o.amount,
               coalesce(o.currency, g.currency, 'BRL') as currency,
               o.status,
               o.due_date,
               o.created_at
        from public.finance_obligations_v2 o
        left join public.players p on p.id = o.player_id
        left join public.users pu on pu.id = p.user_id
        left join public.groups g on g.id = o.group_id
        where o.group_id = cast(:gid as uuid)
          and (o.player_id = cast(:pid as uuid) or o.user_id = cast(:uid as uuid))
        order by coalesce(o.due_date, o.created_at) desc, o.created_at desc
    """), {'gid': group_id, 'pid': identity['player_id'], 'uid': principal.user_id}).mappings().all()

    entries = db.execute(text("""
        select e.id::text as id,
               e.id::text as entry_id,
               e.group_id::text as group_id,
               e.obligation_id::text as obligation_id,
               e.user_id::text as user_id,
               e.player_id::text as player_id,
               e.match_id::text as match_id,
               coalesce(nullif(trim(guest.name), ''), nullif(trim(concat_ws(' ', nullif(trim(pu2.first_name),''), nullif(trim(pu2.last_name),''))), ''), nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(pu2.name), ''), nullif(trim(o.title), ''), nullif(trim(e.notes), ''), 'Jogador') as player_name,
               e.entry_type,
               e.category,
               e.amount,
               coalesce(e.currency, g.currency, 'BRL') as currency,
               e.paid_at,
               e.notes,
               e.created_by_user_id::text as confirmed_by_user_id,
               coalesce(
                   nullif(trim(concat_ws(' ', nullif(trim(actor.first_name),''), nullif(trim(actor.last_name),''))), ''),
                   nullif(trim(actor.name), ''),
                   nullif(trim(actor_player.display_name), ''),
                   nullif(split_part(actor.email, '@', 1), ''),
                   'Jogador'
               ) as confirmed_by_user_name,
               e.created_at
        from public.finance_entries_v2 e
        left join public.finance_obligations_v2 o on o.id = e.obligation_id
        left join public.players p on p.id = coalesce(e.player_id, o.player_id)
        left join public.users pu2 on pu2.id = p.user_id
        left join public.groups g on g.id = e.group_id
        left join public.match_guests_v2 guest on guest.match_id = coalesce(e.match_id, o.match_id) and (e.notes = concat('guest_id:', guest.id::text) or e.notes = coalesce(nullif(trim(guest.name), ''), 'Convidado') or e.notes like concat('guest_id:', guest.id::text, '%') or coalesce(o.description, '') = concat('guest_id:', guest.id::text))
        left join public.users actor on actor.id = e.created_by_user_id
        left join public.players actor_player on actor_player.user_id = actor.id
        where e.group_id = cast(:gid as uuid)
          and (coalesce(e.player_id, o.player_id) = cast(:pid as uuid) or coalesce(e.user_id, o.user_id) = cast(:uid as uuid))
        order by coalesce(e.paid_at, e.created_at) desc
    """), {'gid': group_id, 'pid': identity['player_id'], 'uid': principal.user_id}).mappings().all()

    currency = 'BRL'
    pending_total = 0.0
    monthly_pending = 0.0
    fines_pending = 0.0
    single_charges = 0.0
    recent_ledger = []
    paid_obligation_ids = set()

    for item in entries:
        currency = item.get('currency') or currency
        if item.get('obligation_id'):
            paid_obligation_ids.add(item['obligation_id'])
        recent_ledger.append({
            'id': item['id'],
            'entry_id': item['entry_id'],
            'group_id': item['group_id'],
            'obligation_id': item.get('obligation_id'),
            'user_id': item.get('user_id'),
            'player_id': item.get('player_id'),
            'match_id': item.get('match_id'),
            'entry_type': 'single' if (item.get('category') or '').lower() in {'match_payment', 'single_guest'} else ((item.get('category') or '').lower() if (item.get('category') or '').lower() in {'mensalidade', 'multa', 'venue', 'extra_expense'} else 'manual'),
            'type': 'income' if (item.get('entry_type') or '').lower() != 'outflow' else 'expense',
            'amount': float(item.get('amount') or 0),
            'currency': item.get('currency') or currency,
            'status': 'paid',
            'display_status': 'paid',
            'is_overdue': False,
            'description': item.get('player_name') or item.get('notes') or 'Movimentação',
            'paid': True,
            'user_name': item.get('player_name'),
            'paid_at': item.get('paid_at').isoformat() if item.get('paid_at') else None,
            'confirmed_by_user_id': item.get('confirmed_by_user_id'),
            'confirmed_by_user_name': item.get('confirmed_by_user_name'),
            'can_unmark': bool(item.get('confirmed_by_user_id') and item.get('confirmed_by_user_id') == principal.user_id),
            'created_at': item.get('created_at').isoformat() if item.get('created_at') else None,
        })

    for item in obligations:
        currency = item.get('currency') or currency
        status = (item.get('status') or '').strip().lower()
        if status in {'paga', 'paid', 'cancelled', 'forgiven'}:
            continue
        amount = float(item.get('amount') or 0)
        pending_total += amount
        source_type = (item.get('source_type') or '').strip().lower()
        if source_type == 'mensalidade':
            monthly_pending += amount
        elif source_type == 'multa':
            fines_pending += amount
        elif source_type in {'avulso_partida', 'convidado_partida'}:
            single_charges += amount
        if item['obligation_id'] not in paid_obligation_ids:
            due_date = item.get('due_date')
            due_text = due_date.isoformat()[:10] if hasattr(due_date, 'isoformat') else (str(due_date)[:10] if due_date else None)
            is_overdue = False
            if due_text:
                try:
                    is_overdue = datetime.fromisoformat(due_text).date() < datetime.utcnow().date()
                except Exception:
                    is_overdue = False
            recent_ledger.append({
                'id': item['obligation_id'],
                'entry_id': item['obligation_id'],
                'group_id': item['group_id'],
                'obligation_id': item['obligation_id'],
                'user_id': item.get('user_id'),
                'player_id': item.get('player_id'),
                'match_id': item.get('match_id'),
                'entry_type': 'monthly' if source_type == 'mensalidade' else ('fine' if source_type == 'multa' else ('single' if source_type in {'avulso_partida', 'convidado_partida'} else 'manual')),
                'type': 'income',
                'amount': amount,
                'currency': item.get('currency') or currency,
                'status': 'overdue' if is_overdue else 'pending',
                'display_status': 'overdue' if is_overdue else 'pending',
                'is_overdue': is_overdue,
                'due_date': due_text,
                'description': item.get('player_name') or item.get('title') or item.get('description') or 'Movimentação',
                'paid': False,
                'user_name': item.get('player_name'),
                'confirmed_by_user_id': None,
                'confirmed_by_user_name': None,
                'can_unmark': False,
                'created_at': item.get('created_at').isoformat() if item.get('created_at') else None,
            })

    recent_ledger.sort(key=lambda item: str(item.get('paid_at') or item.get('created_at') or item.get('due_date') or ''), reverse=True)
    paid_total = float(sum(float(item.get('amount') or 0) for item in entries if (item.get('entry_type') or '').lower() == 'inflow'))

    return {
        'group_id': group_id,
        'currency': currency,
        'pending_total': pending_total,
        'paid_total': paid_total,
        'fines_pending': fines_pending,
        'monthly_due': monthly_pending > 0,
        'monthly_pending': monthly_pending,
        'single_charges': single_charges,
        'balance_total': paid_total - pending_total,
        'ledger_count': len(recent_ledger),
        'recent_ledger': recent_ledger[:20],
    }
