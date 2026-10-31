from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

LABEL = 'Isento de mensalidade — goleiro'


def month_start(year: int | None = None, month: int | None = None) -> date:
    today = datetime.now(timezone.utc).date()
    return date(year or today.year, month or today.month, 1)


def next_month(today: date | None = None) -> date:
    current = today or month_start()
    return date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)


class MonthlyExemptionService:
    def states(self, db, *, group_id: str, reference: date | None = None) -> dict[str, dict[str, Any]]:
        reference = reference or month_start()
        rows = db.execute(text('''
            select gm.player_id::text as player_id,
                   coalesce(current_event.enabled, false) as monthly_exempt,
                   next_event.enabled as next_enabled, next_event.effective_month as next_month
            from public.group_members gm join public.groups gr on gr.id=gm.group_id
            left join lateral (
                select e.enabled from public.monthly_exemption_events_v2 e
                where e.membership_id=gm.id and e.membership_epoch=gm.monthly_exemption_epoch
                  and e.effective_month <= :reference
                order by e.effective_month desc, e.sequence_no desc limit 1
            ) current_event on true
            left join lateral (
                select e.enabled, e.effective_month from public.monthly_exemption_events_v2 e
                where e.membership_id=gm.id and e.membership_epoch=gm.monthly_exemption_epoch
                  and e.effective_month > :reference
                order by e.effective_month asc, e.sequence_no desc limit 1
            ) next_event on true
            where gm.group_id=cast(:group_id as uuid) and gm.status::text='active'
              and lower(gr.group_type::text) in ('hibrido','híbrido','hybrid')
        '''), {'group_id': group_id, 'reference': reference}).mappings().all()
        result = {}
        for row in rows:
            enabled = bool(row['monthly_exempt'])
            result[row['player_id']] = {
                'monthly_exempt': enabled,
                'monthly_exemption_label': LABEL if enabled else None,
                'monthly_exemption_pending': (
                    {'enabled': bool(row['next_enabled']), 'effective_month': row['next_month'].isoformat()[:7]}
                    if row['next_month'] else None
                ),
            }
        return result

    def can_generate(self, db, *, group_id: str, player_id: str, reference: date) -> bool:
        # The same row lock is used by configuration changes, preventing an
        # invoice and an exemption from racing for the same membership.
        member = db.execute(text('''
            select gm.id::text as membership_id, gm.monthly_exemption_epoch::text as epoch,
                   gm.monthly_exemption_epoch_started_at, gm.role::text as role,
                   gm.status::text as status, gm.billing_type::text as billing_type
            from public.group_members gm join public.groups gr on gr.id=gm.group_id
            where gm.group_id=cast(:group_id as uuid) and gm.player_id=cast(:player_id as uuid)
              and lower(gr.group_type::text) in ('hibrido','híbrido','hybrid')
            for update of gm
        '''), {'group_id': group_id, 'player_id': player_id}).mappings().first()
        if not member or member['status'] != 'active' or member['role'] == 'owner':
            return False
        if member['billing_type'] not in {'mensalista', 'monthly'}:
            return False
        joined = member['monthly_exemption_epoch_started_at']
        if joined and reference < date(joined.year, joined.month, 1):
            return False
        enabled = db.execute(text('''
            select enabled from public.monthly_exemption_events_v2
            where membership_id=cast(:membership_id as uuid) and membership_epoch=cast(:epoch as uuid)
              and effective_month <= :reference
            order by effective_month desc, sequence_no desc limit 1
        '''), {'membership_id': member['membership_id'], 'epoch': member['epoch'], 'reference': reference}).scalar()
        return not bool(enabled)

    def change(self, db, *, group_id: str, target_user_id: str, actor_user_id: str,
               enabled: bool, effective_month: date) -> dict:
        current = month_start()
        group_type = db.execute(text('select group_type::text from public.groups where id=cast(:id as uuid)'), {'id': group_id}).scalar()
        if (group_type or '').lower() not in {'hibrido', 'híbrido', 'hybrid'}:
            raise HTTPException(422, 'A isenção é exclusiva da mensalidade de grupos híbridos.')
        role = db.execute(text('''
            select gm.role::text from public.group_members gm join public.players p on p.id=gm.player_id
            where gm.group_id=cast(:group_id as uuid) and p.user_id=cast(:actor as uuid)
              and gm.status::text='active'
        '''), {'group_id': group_id, 'actor': actor_user_id}).scalar()
        if role not in {'owner', 'admin'}:
            raise HTTPException(403, 'Somente owner ou admin podem configurar a isenção de mensalidade.')
        if effective_month.day != 1 or effective_month < current:
            raise HTTPException(422, 'Escolha a competência atual ou uma competência futura.')
        member = db.execute(text('''
            select gm.id::text as membership_id, gm.player_id::text as player_id,
                   gm.monthly_exemption_epoch::text as epoch
            from public.group_members gm join public.players p on p.id=gm.player_id
            where gm.group_id=cast(:group_id as uuid) and p.user_id=cast(:user_id as uuid)
              and gm.status::text='active' for update of gm
        '''), {'group_id': group_id, 'user_id': target_user_id}).mappings().first()
        if not member:
            raise HTTPException(404, 'Membro não encontrado neste grupo.')
        issued = db.execute(text('''
            select exists(select 1 from public.finance_obligations_v2
                where group_id=cast(:group_id as uuid) and player_id=cast(:player_id as uuid)
                  and source_type='mensalidade'
                  and competence_year*100 + competence_month >= :competence)
        '''), {'group_id': group_id, 'player_id': member['player_id'],
               'competence': effective_month.year * 100 + effective_month.month}).scalar()
        if issued:
            raise HTTPException(409, 'Já há mensalidade emitida nessa competência ou em competência posterior. Escolha uma competência posterior às cobranças emitidas; o histórico será preservado.')
        db.execute(text('''
            insert into public.monthly_exemption_events_v2
                (membership_id,membership_epoch,group_id,player_id,enabled,effective_month,actor_user_id)
            values(cast(:membership_id as uuid),cast(:epoch as uuid),cast(:group_id as uuid),
                cast(:player_id as uuid),:enabled,:effective_month,cast(:actor as uuid))
        '''), {**dict(member), 'group_id': group_id, 'enabled': enabled, 'effective_month': effective_month, 'actor': actor_user_id})
        return {'player_id': member['player_id'], 'enabled': enabled,
                'effective_month': effective_month.isoformat()[:7], 'reason': 'goalkeeper'}
