from __future__ import annotations

from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session


class FinanceV2Repository:
    def fetch_foundation_identity(self, db: Session, *, user_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select u.id::text as user_id, u.email as user_email, p.id::text as player_id,
                   coalesce(
                       nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(u.name), ''),
                       'Jogador'
                   ) as display_name
            from public.users u
            join public.players p on p.user_id = u.id
            where u.id = cast(:user_id as uuid)
            limit 1
        """), {'user_id': user_id}).mappings().first()
        return dict(row) if row else None

    def fetch_membership(self, db: Session, *, group_id: str, user_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select gm.role::text as role, gm.status::text as status, gm.billing_type::text as billing_type,
                   p.id::text as player_id,
                   coalesce(
                       nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(u.name), ''),
                       'Jogador'
                   ) as display_name
            from public.group_members gm
            join public.players p on p.id = gm.player_id
            join public.users u on u.id = p.user_id
            where gm.group_id = cast(:group_id as uuid)
              and p.user_id = cast(:user_id as uuid)
            limit 1
        """), {'group_id': group_id, 'user_id': user_id}).mappings().first()
        return dict(row) if row else None



    def list_user_finance_groups(self, db: Session, *, user_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select g.id::text as group_id,
                   g.name as group_name,
                   coalesce(g.currency, 'BRL') as currency,
                   gm.role::text as role,
                   gm.status::text as status,
                   gm.billing_type::text as billing_type,
                   p.id::text as player_id,
                   u.id::text as user_id
            from public.group_members gm
            join public.players p on p.id = gm.player_id
            join public.users u on u.id = p.user_id
            join public.groups g on g.id = gm.group_id
            where u.id = cast(:user_id as uuid)
              and gm.status = cast('active' as membership_status_enum)
            order by lower(g.name) asc
        """), {'user_id': user_id}).mappings().all()
        return [dict(r) for r in rows]

    def fetch_member_wallet_snapshot(self, db: Session, *, group_id: str, player_id: str) -> dict[str, Any]:
        row = db.execute(text("""
            with pending as (
                select coalesce(sum(amount), 0) as pending_total
                from public.finance_obligations_v2
                where group_id = cast(:group_id as uuid)
                  and player_id = cast(:player_id as uuid)
                  and status in ('aberta', 'parcial')
            ),
            paid as (
                select coalesce(sum(amount), 0) as paid_total
                from public.finance_entries_v2
                where group_id = cast(:group_id as uuid)
                  and player_id = cast(:player_id as uuid)
                  and entry_type = 'inflow'
            )
            select pending.pending_total,
                   paid.paid_total,
                   (paid.paid_total - pending.pending_total) as balance_total
            from pending, paid
        """), {'group_id': group_id, 'player_id': player_id}).mappings().first()
        return dict(row or {})
    def fetch_group_finance_context(self, db: Session, *, group_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select id::text as group_id, name, group_type::text as group_type, coalesce(currency, 'BRL') as currency,
                   coalesce(monthly_cost, 0) as monthly_cost,
                   coalesce(single_cost, 0) as single_cost,
                   coalesce(venue_cost, 0) as venue_cost,
                   coalesce(fine_amount, 0) as fine_amount,
                   payment_method, payment_key, payment_due_day
            from public.groups
            where id = cast(:group_id as uuid)
            limit 1
        """), {'group_id': group_id}).mappings().first()
        return dict(row) if row else None

    def fetch_match_context(self, db: Session, *, group_id: str, match_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select id::text as match_id, group_id::text as group_id, title,
                   starts_at, ends_at, status::text as status
            from public.matches_v2
            where id = cast(:match_id as uuid)
              and group_id = cast(:group_id as uuid)
            limit 1
        """), {'group_id': group_id, 'match_id': match_id}).mappings().first()
        return dict(row) if row else None

    def fetch_finance_summary(self, db: Session, *, group_id: str) -> dict[str, Any]:
        row = db.execute(text("""
            with totals as (
                select
                    coalesce(sum(case when entry_type = 'inflow' then amount else 0 end), 0) as received,
                    coalesce(sum(case when entry_type = 'outflow' then amount else 0 end), 0) as expenses,
                    count(*)::int as entries_count
                from public.finance_entries_v2
                where group_id = cast(:group_id as uuid)
            ),
            obligations as (
                select coalesce(sum(amount), 0) as open_amount, count(*)::int as obligations_count
                from public.finance_obligations_v2
                where group_id = cast(:group_id as uuid)
                  and status in ('aberta', 'parcial')
            )
            select totals.received, totals.expenses, (totals.received - totals.expenses) as balance,
                   obligations.open_amount, obligations.obligations_count, totals.entries_count
            from totals, obligations
        """), {'group_id': group_id}).mappings().first()
        return dict(row or {})

    def list_obligations(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select o.id::text as obligation_id, o.group_id::text as group_id, o.user_id::text as user_id,
                   o.player_id::text as player_id, o.match_id::text as match_id,
                   coalesce(
                       nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(u.name), ''),
                       'Jogador'
                   ) as player_name,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as player_avatar_url,
                   o.source_type, o.title, o.description, o.amount, o.currency, o.status,
                   o.due_date, o.competence_month, o.competence_year, o.created_at
            from public.finance_obligations_v2 o
            left join public.players p on p.id = o.player_id
            left join public.users u on u.id = p.user_id
            where o.group_id = cast(:group_id as uuid)
            order by o.created_at desc, o.title asc
        """), {'group_id': group_id}).mappings().all()
        return [dict(r) for r in rows]

    def list_entries(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select e.id::text as entry_id, e.id::text as id,
                   e.group_id::text as group_id, e.obligation_id::text as obligation_id,
                   e.user_id::text as user_id, e.player_id::text as player_id, e.match_id::text as match_id,
                   coalesce(
                       nullif(trim(guest.name), ''),
                       nullif(trim(concat_ws(' ', nullif(trim(pu.first_name), ''), nullif(trim(pu.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(pu.name), ''),
                       nullif(trim(o.title), ''),
                       nullif(trim(e.notes), ''),
                       'Jogador'
                   ) as player_name,
                   coalesce(
                       nullif(trim(guest.name), ''),
                       nullif(trim(concat_ws(' ', nullif(trim(pu.first_name), ''), nullif(trim(pu.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(pu.name), ''),
                       nullif(trim(o.title), ''),
                       nullif(trim(e.notes), ''),
                       'Jogador'
                   ) as user_name,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(pu.avatar_url), '')) as user_avatar_url,
                   e.entry_type, e.category, e.amount, e.currency,
                   coalesce(o.status, 'paga') as obligation_status,
                   o.source_type as obligation_source_type, o.due_date, o.title as obligation_title, o.description as obligation_description,
                   nullif(trim(guest.name), '') as guest_name,
                   coalesce(
                       nullif(trim(concat_ws(' ', actor.first_name, actor.last_name)), ''),
                       nullif(trim(actor.name), ''),
                       nullif(trim(actor_player.display_name), ''),
                       nullif(split_part(actor.email, '@', 1), ''),
                       'Jogador'
                   ) as confirmed_by_name,
                   e.paid_at, e.notes, e.created_by_user_id::text as created_by_user_id, e.created_at
            from public.finance_entries_v2 e
            left join public.finance_obligations_v2 o on o.id = e.obligation_id
            left join public.players p on p.id = coalesce(e.player_id, o.player_id)
            left join public.users pu on pu.id = p.user_id
            left join public.match_guests_v2 guest
              on guest.match_id = coalesce(e.match_id, o.match_id)
             and (
                    e.notes = concat('guest_id:', guest.id::text)
                 or e.notes = coalesce(nullif(trim(guest.name), ''), 'Convidado')
                 or e.notes like concat('guest_id:', guest.id::text, '%')
                 or coalesce(o.description, '') = concat('guest_id:', guest.id::text)
             )
            left join public.users actor on actor.id = e.created_by_user_id
            left join public.players actor_player on actor_player.user_id = actor.id
            where e.group_id = cast(:group_id as uuid)
            order by coalesce(e.paid_at, e.created_at) desc
        """), {'group_id': group_id}).mappings().all()
        return [dict(r) for r in rows]

    def list_ledger(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select id::text as ledger_id, movement_type, direction, amount, balance_impact,
                   description, reference_date, created_at
            from public.finance_ledger_v2
            where group_id = cast(:group_id as uuid)
            order by reference_date desc, created_at desc
        """), {'group_id': group_id}).mappings().all()
        return [dict(r) for r in rows]

    def list_active_monthly_members(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select u.id::text as user_id, p.id::text as player_id,
                   coalesce(
                       nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(u.name), ''),
                       'Jogador'
                   ) as display_name,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url
            from public.group_members gm
            join public.players p on p.id = gm.player_id
            join public.users u on u.id = p.user_id
            where gm.group_id = cast(:group_id as uuid)
              and gm.status = cast('active' as membership_status_enum)
              and gm.billing_type = cast('mensalista' as billing_type_enum)
              and coalesce(gm.role::text, '') <> 'owner'
            order by display_name asc
        """), {'group_id': group_id}).mappings().all()
        return [dict(r) for r in rows]

    def obligation_exists_for_monthly(self, db: Session, *, group_id: str, player_id: str, month: int, year: int) -> bool:
        return bool(db.execute(text("""
            select exists(
                select 1 from public.finance_obligations_v2
                where group_id = cast(:group_id as uuid)
                  and player_id = cast(:player_id as uuid)
                  and source_type = 'mensalidade'
                  and competence_month = :month
                  and competence_year = :year
            )
        """), {'group_id': group_id, 'player_id': player_id, 'month': month, 'year': year}).scalar())

    def create_obligation(self, db: Session, *, payload: dict[str, Any]) -> str:
        return db.execute(text("""
            insert into public.finance_obligations_v2 (
                id, group_id, user_id, player_id, match_id, source_type, title, description,
                amount, currency, status, due_date, competence_month, competence_year, created_by_user_id,
                created_at, updated_at
            ) values (
                gen_random_uuid(), cast(:group_id as uuid), cast(:user_id as uuid), cast(:player_id as uuid),
                cast(:match_id as uuid), :source_type, :title, :description, :amount, :currency, :status,
                :due_date, :competence_month, :competence_year, cast(:created_by_user_id as uuid), now(), now()
            ) returning id::text
        """), payload).scalar_one()

    def list_match_charge_candidates(self, db: Session, *, match_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select mp.id::text as participant_id, mp.user_id::text as user_id, mp.player_id::text as player_id,
                   coalesce(
                       nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(u.name), ''),
                       'Jogador'
                   ) as display_name
            from public.match_participants_v2 mp
            join public.players p on p.id = mp.player_id
            join public.users u on u.id = p.user_id
            where mp.match_id = cast(:match_id as uuid)
              and mp.status = cast('confirmado' as match_presence_status_enum_v2)
              and mp.has_arrived = true
            order by display_name asc
        """), {'match_id': match_id}).mappings().all()
        return [dict(r) for r in rows]

    def obligation_exists_for_match_player(self, db: Session, *, match_id: str, player_id: str) -> bool:
        return bool(db.execute(text("""
            select exists(
                select 1 from public.finance_obligations_v2
                where match_id = cast(:match_id as uuid)
                  and player_id = cast(:player_id as uuid)
                  and source_type = 'avulso_partida'
            )
        """), {'match_id': match_id, 'player_id': player_id}).scalar())

    def entry_exists_for_match_court(self, db: Session, *, match_id: str) -> bool:
        return bool(db.execute(text("""
            select exists(
                select 1 from public.finance_entries_v2
                where match_id = cast(:match_id as uuid)
                  and category = 'quadra'
                  and entry_type = 'outflow'
            )
        """), {'match_id': match_id}).scalar())

    def create_entry(self, db: Session, *, payload: dict[str, Any]) -> str:
        return db.execute(text("""
            insert into public.finance_entries_v2 (
                id, group_id, obligation_id, user_id, player_id, match_id, entry_type, category,
                amount, currency, paid_at, notes, created_by_user_id, created_at
            ) values (
                gen_random_uuid(), cast(:group_id as uuid), cast(:obligation_id as uuid), cast(:user_id as uuid),
                cast(:player_id as uuid), cast(:match_id as uuid), :entry_type, :category, :amount, :currency,
                coalesce(:paid_at, now()), :notes, cast(:created_by_user_id as uuid), now()
            ) returning id::text
        """), payload).scalar_one()

    def create_ledger(self, db: Session, *, payload: dict[str, Any]) -> None:
        db.execute(text("""
            insert into public.finance_ledger_v2 (
                id, group_id, obligation_id, entry_id, movement_type, direction,
                amount, balance_impact, description, reference_date, created_at
            ) values (
                gen_random_uuid(), cast(:group_id as uuid), cast(:obligation_id as uuid), cast(:entry_id as uuid),
                :movement_type, :direction, :amount, :balance_impact, :description, coalesce(:reference_date, now()), now()
            )
        """), payload)

    def fetch_obligation(self, db: Session, *, group_id: str, obligation_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select id::text as obligation_id, group_id::text as group_id, user_id::text as user_id,
                   player_id::text as player_id, match_id::text as match_id, source_type, title,
                   description, amount, currency, status, due_date, competence_month, competence_year, created_at
            from public.finance_obligations_v2
            where id = cast(:obligation_id as uuid)
              and group_id = cast(:group_id as uuid)
            limit 1
        """), {'group_id': group_id, 'obligation_id': obligation_id}).mappings().first()
        return dict(row) if row else None

    def mark_obligation_paid(self, db: Session, *, obligation_id: str) -> None:
        db.execute(text("""
            update public.finance_obligations_v2
            set status = 'paga', updated_at = now()
            where id = cast(:obligation_id as uuid)
        """), {'obligation_id': obligation_id})

    def fetch_entry(self, db: Session, *, group_id: str, entry_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select id::text as entry_id, id::text as id,
                   group_id::text as group_id, obligation_id::text as obligation_id,
                   user_id::text as user_id, player_id::text as player_id, match_id::text as match_id,
                   entry_type, category, amount, currency, paid_at, notes, created_by_user_id::text as created_by_user_id,
                   created_at
            from public.finance_entries_v2
            where id = cast(:entry_id as uuid)
              and group_id = cast(:group_id as uuid)
            limit 1
        """), {'group_id': group_id, 'entry_id': entry_id}).mappings().first()
        return dict(row) if row else None

    def delete_entry(self, db: Session, *, group_id: str, entry_id: str) -> None:
        db.execute(text("""
            delete from public.finance_ledger_v2
            where group_id = cast(:group_id as uuid)
              and entry_id = cast(:entry_id as uuid)
        """), {'group_id': group_id, 'entry_id': entry_id})
        db.execute(text("""
            delete from public.finance_entries_v2
            where group_id = cast(:group_id as uuid)
              and id = cast(:entry_id as uuid)
        """), {'group_id': group_id, 'entry_id': entry_id})

    def update_obligation_status(self, db: Session, *, obligation_id: str, status: str) -> None:
        db.execute(text("""
            update public.finance_obligations_v2
            set status = :status, updated_at = now()
            where id = cast(:obligation_id as uuid)
        """), {'obligation_id': obligation_id, 'status': status})


    def list_active_billing_members(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select u.id::text as user_id,
                   p.id::text as player_id,
                   coalesce(
                       nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                       nullif(trim(p.display_name), ''),
                       nullif(trim(p.full_name), ''),
                       nullif(trim(u.name), ''),
                       'Jogador'
                   ) as player_name,
                   coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url,
                   gm.billing_type::text as billing_type
            from public.group_members gm
            join public.players p on p.id = gm.player_id
            join public.users u on u.id = p.user_id
            where gm.group_id = cast(:group_id as uuid)
              and gm.status = cast('active' as membership_status_enum)
              and coalesce(gm.role::text, '') <> 'owner'
            order by lower(player_name) asc
        """), {'group_id': group_id}).mappings().all()
        return [dict(r) for r in rows]

    def fetch_monthly_obligation(self, db: Session, *, group_id: str, player_id: str, year: int, month: int) -> dict[str, Any] | None:
        row = db.execute(text("""
            select id::text as obligation_id,
                   group_id::text as group_id,
                   user_id::text as user_id,
                   player_id::text as player_id,
                   match_id::text as match_id,
                   source_type,
                   title,
                   description,
                   amount,
                   currency,
                   status,
                   due_date,
                   competence_month,
                   competence_year,
                   created_by_user_id::text as created_by_user_id,
                   created_at
            from public.finance_obligations_v2
            where group_id = cast(:group_id as uuid)
              and player_id = cast(:player_id as uuid)
              and source_type = 'mensalidade'
              and competence_year = :year
              and competence_month = :month
            order by created_at desc
            limit 1
        """), {'group_id': group_id, 'player_id': player_id, 'year': year, 'month': month}).mappings().first()
        return dict(row) if row else None

    def fetch_paid_entry_for_obligation(self, db: Session, *, group_id: str, obligation_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select e.id::text as entry_id,
                   e.id::text as id,
                   e.group_id::text as group_id,
                   e.obligation_id::text as obligation_id,
                   e.user_id::text as user_id,
                   e.player_id::text as player_id,
                   e.match_id::text as match_id,
                   e.entry_type,
                   e.category,
                   e.amount,
                   e.currency,
                   e.paid_at,
                   e.notes,
                   e.created_by_user_id::text as created_by_user_id,
                   e.created_at
            from public.finance_entries_v2 e
            where e.group_id = cast(:group_id as uuid)
              and e.obligation_id = cast(:obligation_id as uuid)
              and e.entry_type = 'inflow'
            order by coalesce(e.paid_at, e.created_at) desc
            limit 1
        """), {'group_id': group_id, 'obligation_id': obligation_id}).mappings().first()
        return dict(row) if row else None

    def fetch_obligation_by_reference(self, db: Session, *, group_id: str, reference_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select id::text as obligation_id,
                   group_id::text as group_id,
                   user_id::text as user_id,
                   player_id::text as player_id,
                   match_id::text as match_id,
                   source_type,
                   title,
                   description,
                   amount,
                   currency,
                   status,
                   due_date,
                   competence_month,
                   competence_year,
                   created_by_user_id::text as created_by_user_id,
                   created_at
            from public.finance_obligations_v2
            where group_id = cast(:group_id as uuid)
              and id = cast(:reference_id as uuid)
            limit 1
        """), {'group_id': group_id, 'reference_id': reference_id}).mappings().first()
        return dict(row) if row else None

    def list_match_guest_charge_candidates(self, db: Session, *, match_id: str) -> list[dict[str, Any]]:
        rows = db.execute(text("""
            select g.id::text as guest_id,
                   coalesce(nullif(trim(g.name), ''), 'Convidado') as guest_name
            from public.match_guests_v2 g
            where g.match_id = cast(:match_id as uuid)
              and g.status = cast('confirmado' as match_presence_status_enum_v2)
              and g.has_arrived = true
            order by lower(coalesce(nullif(trim(g.name), ''), 'Convidado')) asc
        """), {'match_id': match_id}).mappings().all()
        return [dict(r) for r in rows]

    def obligation_exists_for_match_guest(self, db: Session, *, match_id: str, guest_id: str) -> bool:
        return bool(db.execute(text("""
            select exists(
                select 1 from public.finance_obligations_v2
                where match_id = cast(:match_id as uuid)
                  and source_type = 'convidado_partida'
                  and description = :description
            )
        """), {'match_id': match_id, 'description': f'guest_id:{guest_id}'}).scalar())
