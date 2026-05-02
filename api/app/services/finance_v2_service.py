from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.core.cache import app_cache
from app.repositories.finance_v2 import FinanceV2Repository
from app.services.notifications_v2_service import NotificationsV2Service
from app.schemas.finance_v2 import (
    FinanceV2AutomationStatusModel,
    FinanceV2BillingMembersModel,
    FinanceV2CreateEntryRequest,
    FinanceV2EntryModel,
    FinanceV2GenerateMatchResult,
    FinanceV2LedgerModel,
    FinanceV2ManualTransactionRequest,
    FinanceV2MarkPaidRequest,
    FinanceV2MonthlyGenerateRequest,
    FinanceV2MonthlyGenerateResult,
    FinanceV2MonthlyMemberStatusModel,
    FinanceV2ObligationModel,
    FinanceV2SettingsRequest,
    FinanceV2SingleMemberStatusModel,
    FinanceV2SummaryModel,
)


class FinanceV2Service:
    def _cache_key(self, kind: str, group_id: str, user_id: str, *, year: int | None = None, month: int | None = None) -> str:
        suffix = ''
        if year is not None or month is not None:
            suffix = f':year:{year or "current"}:month:{month or "current"}'
        return f'finance_v2:{kind}:group:{group_id}:user:{user_id}{suffix}'

    def _invalidate_group_cache(self, *, group_id: str) -> None:
        app_cache.invalidate_prefix(f"finance_v2:summary:group:{group_id}")
        app_cache.invalidate_prefix(f"finance_v2:obligations:group:{group_id}")
        app_cache.invalidate_prefix(f"finance_v2:entries:group:{group_id}")
        app_cache.invalidate_prefix(f"finance_v2:ledger:group:{group_id}")

    def __init__(self, repository: FinanceV2Repository | None = None) -> None:
        self.repository = repository or FinanceV2Repository()
        self.notifications = NotificationsV2Service()

    def _identity_or_404(self, db: Session, principal: SupabasePrincipal) -> dict:
        identity = self.repository.fetch_foundation_identity(db, user_id=principal.user_id)
        if not identity:
            raise HTTPException(status_code=404, detail='Sessão BoraFut não bootstrapada para este usuário.')
        return identity

    def _require_active_membership(self, db: Session, *, group_id: str, user_id: str) -> dict:
        membership = self.repository.fetch_membership(db, group_id=group_id, user_id=user_id)
        if not membership or membership.get('status') != 'active':
            raise HTTPException(status_code=403, detail='Você ainda não é membro ativo deste grupo.')
        return membership

    def _require_admin(self, db: Session, *, group_id: str, user_id: str) -> dict:
        membership = self._require_active_membership(db, group_id=group_id, user_id=user_id)
        if membership.get('role') not in {'owner', 'admin'}:
            raise HTTPException(status_code=403, detail='Somente owner ou admin podem executar esta ação.')
        return membership

    def _group_or_404(self, db: Session, *, group_id: str) -> dict:
        group = self.repository.fetch_group_finance_context(db, group_id=group_id)
        if not group:
            raise HTTPException(status_code=404, detail='Grupo não encontrado.')
        return group

    def _source_to_public_entry_type(self, source_type: str | None, category: str | None = None, entry_type: str | None = None) -> str:
        raw_source = (source_type or '').strip().lower()
        raw_category = (category or '').strip().lower()
        raw_entry_type = (entry_type or '').strip().lower()
        if raw_source == 'mensalidade':
            return 'monthly'
        if raw_source in {'avulso_partida', 'single_match_payment', 'avulso', 'convidado_partida'}:
            return 'single'
        if raw_source == 'multa' or raw_category in {'fine', 'multa'}:
            return 'fine'
        if raw_category in {'match_payment', 'single_guest', 'guest_single'}:
            return 'single'
        if raw_entry_type == 'outflow' or raw_category in {'quadra', 'venue', 'venue_cost'}:
            return 'venue' if raw_category in {'quadra', 'venue', 'venue_cost'} else 'extra_expense'
        if raw_entry_type == 'outflow':
            return 'extra_expense'
        if raw_source in {'manual', 'ajuste_credito', 'ajuste_debito'}:
            return 'manual'
        return 'manual'

    def _public_type(self, public_entry_type: str) -> str:
        return 'expense' if public_entry_type in {'venue', 'extra_expense', 'debit_adjustment'} else 'income'

    def _normalize_group_type(self, value: str | None) -> str:
        normalized = (value or '').strip().lower()
        if 'hibrid' in normalized or 'hybrid' in normalized:
            return 'hibrido'
        if 'avulso' in normalized:
            return 'avulso'
        return normalized

    def _is_hybrid_group(self, group: dict | None) -> bool:
        return self._normalize_group_type((group or {}).get('group_type')) == 'hibrido'

    def _month_matches(self, item: dict, *, year: int, month: int) -> bool:
        competence_year = item.get('competence_year')
        competence_month = item.get('competence_month')
        if competence_year and competence_month:
            return int(competence_year) == year and int(competence_month) == month
        for key in ('paid_at', 'due_date', 'created_at'):
            value = item.get(key)
            if value is None:
                continue
            if isinstance(value, datetime):
                dt = value.date()
            elif isinstance(value, date):
                dt = value
            elif isinstance(value, str):
                try:
                    dt = datetime.fromisoformat(value.replace('Z', '+00:00')).date()
                except Exception:
                    try:
                        dt = date.fromisoformat(value[:10])
                    except Exception:
                        continue
            else:
                continue
            return dt.year == year and dt.month == month
        return False



    def get_quick_access_groups(self, db: Session, principal: SupabasePrincipal) -> list[dict]:
        identity = self._identity_or_404(db, principal)
        memberships = self.repository.list_user_finance_groups(db, user_id=principal.user_id)
        items: list[dict] = []
        for membership in memberships:
            group_id = membership.get('group_id')
            if not group_id:
                continue
            item = {
                'group_id': group_id,
                'group_name': membership.get('group_name') or 'Grupo',
                'currency': membership.get('currency') or 'BRL',
                'role': membership.get('role') or 'member',
                'status': membership.get('status') or 'active',
            }
            role = (membership.get('role') or '').strip().lower()
            if role in {'owner', 'admin'}:
                summary = self.repository.fetch_finance_summary(db, group_id=group_id)
                balance = float(summary.get('balance') or 0)
                item['group_balance'] = balance
                item['health_status'] = 'saudável' if balance >= 0 else 'atenção'
            else:
                wallet = self.repository.fetch_member_wallet_snapshot(db, group_id=group_id, player_id=membership.get('player_id') or identity.get('player_id'))
                pending_total = float(wallet.get('pending_total') or 0)
                balance_total = float(wallet.get('balance_total') or 0)
                status = 'adimplente'
                if pending_total > 0 or balance_total < 0:
                    status = 'devedor'
                elif balance_total > 0:
                    status = 'credito'
                item['my_pending_total'] = pending_total
                item['my_balance_total'] = balance_total
                item['my_financial_status'] = status
            items.append(item)
        return items
    def _build_summary_payload(self, *, group: dict, entries: list[dict], obligations: list[dict], year: int, month: int) -> dict:
        total_income_paid = 0.0
        total_expense_paid = 0.0
        total_pending = 0.0
        next_due_date: date | None = None
        month_income_by_type = {
            'monthly': 0.0,
            'single': 0.0,
            'fine': 0.0,
            'credit_adjustment': 0.0,
        }
        month_expense_by_type = {
            'venue': 0.0,
            'extra_expense': 0.0,
            'debit_adjustment': 0.0,
        }

        for obligation in obligations:
            status = (obligation.get('status') or '').strip().lower()
            if status not in {'paga', 'paid', 'cancelled', 'forgiven', 'excluida', 'cancelada'}:
                total_pending += float(obligation.get('amount') or 0)
                due = obligation.get('due_date')
                if isinstance(due, datetime):
                    due = due.date()
                if isinstance(due, date):
                    if next_due_date is None or due < next_due_date:
                        next_due_date = due

        for entry in entries:
            amount = float(entry.get('amount') or 0)
            raw_entry_type = (entry.get('entry_type') or '').strip().lower()
            public_entry_type = self._source_to_public_entry_type(
                entry.get('obligation_source_type'),
                entry.get('category'),
                raw_entry_type,
            )
            if raw_entry_type == 'outflow':
                total_expense_paid += amount
                if self._month_matches(entry, year=year, month=month):
                    if public_entry_type == 'venue':
                        month_expense_by_type['venue'] += amount
                    else:
                        month_expense_by_type['extra_expense'] += amount
            else:
                total_income_paid += amount
                obligation = next((o for o in obligations if o.get('obligation_id') == entry.get('obligation_id')), None)
                obligation_source = obligation.get('source_type') if obligation else None
                public_entry_type = self._source_to_public_entry_type(obligation_source, entry.get('category'), raw_entry_type)
                if self._month_matches(obligation or entry, year=year, month=month):
                    if public_entry_type == 'monthly':
                        month_income_by_type['monthly'] += amount
                    elif public_entry_type == 'single':
                        month_income_by_type['single'] += amount
                    elif public_entry_type == 'fine':
                        month_income_by_type['fine'] += amount
                    else:
                        month_income_by_type['credit_adjustment'] += amount

        received_subtotal = sum(month_income_by_type.values())
        expenses_subtotal = sum(month_expense_by_type.values())
        balance = total_income_paid - total_expense_paid
        reference_month = date(year, month, 1)
        snapshot_generated_at = datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'

        is_hybrid_group = self._is_hybrid_group(group)

        return {
            'group_id': group['group_id'],
            'currency': group.get('currency') or 'BRL',
            'payment_method': group.get('payment_method'),
            'payment_key': group.get('payment_key'),
            'payment_due_day': group.get('payment_due_day') if is_hybrid_group else None,
            'total_paid': total_income_paid,
            'total_pending': total_pending,
            'next_due_date': next_due_date.isoformat() if next_due_date else None,
            'cashflow_total': balance,
            'monthly_members_total': month_income_by_type['monthly'] if is_hybrid_group else 0.0,
            'single_matches_total': month_income_by_type['single'],
            'fines_total': month_income_by_type['fine'],
            'venue_total': month_expense_by_type['venue'],
            'extra_expenses_total': month_expense_by_type['extra_expense'],
            'received_subtotal': received_subtotal,
            'expenses_subtotal': expenses_subtotal,
            'month_result': received_subtotal - expenses_subtotal,
            'cash_in_box': balance,
            'total_income_paid': total_income_paid,
            'total_expense_paid': total_expense_paid,
            'month_year': year,
            'month_month': month,
            'snapshot_reference_month': reference_month.isoformat(),
            'snapshot_generated_at': snapshot_generated_at,
            'balance': balance,
            'received': total_income_paid,
            'expenses': total_expense_paid,
            'open_amount': total_pending,
            'obligations_count': len([o for o in obligations if (o.get('status') or '').strip().lower() not in {'paga', 'paid', 'cancelled', 'forgiven', 'excluida', 'cancelada'}]),
            'entries_count': len(entries),
        }

    def _to_entry_model(self, item: dict, *, current_user_id: str) -> FinanceV2EntryModel:
        raw_entry_type = (item.get('entry_type') or '').strip().lower()
        obligation_status = (item.get('obligation_status') or 'paga').strip().lower()
        public_entry_type = self._source_to_public_entry_type(
            item.get('obligation_source_type'),
            item.get('category'),
            raw_entry_type,
        )
        due_date = item.get('due_date')
        overdue = False
        if isinstance(due_date, datetime):
            overdue = due_date.date() < datetime.utcnow().date()
        elif isinstance(due_date, date):
            overdue = due_date < datetime.utcnow().date()
        elif isinstance(due_date, str):
            try:
                overdue = date.fromisoformat(due_date[:10]) < datetime.utcnow().date()
            except Exception:
                overdue = False
        display_status = 'paid' if raw_entry_type in {'inflow', 'outflow'} else ('overdue' if overdue else 'pending')
        if isinstance(due_date, (date, datetime)):
            due_date = due_date.isoformat()[:10]
        description = (item.get('guest_name') or item.get('player_name') or item.get('obligation_title') or item.get('obligation_description') or item.get('notes') or item.get('category'))
        paid = raw_entry_type in {'inflow', 'outflow'}
        confirmed_by_user_id = item.get('created_by_user_id')
        confirmed_by_user_name = item.get('confirmed_by_name')
        return FinanceV2EntryModel(
            entry_id=item['entry_id'],
            id=item['id'],
            group_id=item['group_id'],
            obligation_id=item.get('obligation_id'),
            user_id=item.get('user_id'),
            player_id=item.get('player_id'),
            match_id=item.get('match_id'),
            player_name=item.get('player_name'),
            user_name=item.get('user_name') or item.get('player_name'),
            user_avatar_url=item.get('user_avatar_url'),
            entry_type=public_entry_type,
            type=self._public_type(public_entry_type),
            category=item.get('category') or '',
            amount=float(item.get('amount') or 0),
            currency=item.get('currency') or 'BRL',
            status='paid' if paid else ('pending' if obligation_status in {'aberta', 'parcial', 'pending'} else obligation_status),
            display_status=display_status,
            is_overdue=display_status == 'overdue',
            due_date=due_date,
            description=description,
            paid=paid,
            paid_at=item.get('paid_at'),
            paid_amount=float(item.get('amount') or 0) if paid else None,
            payment_method=None,
            notes=item.get('notes'),
            confirmed_by_user_id=confirmed_by_user_id,
            confirmed_by_user_name=confirmed_by_user_name,
            can_unmark=bool(confirmed_by_user_id and current_user_id and confirmed_by_user_id == current_user_id),
            created_at=item.get('created_at'),
        )

    def _notify_group(self, db: Session, *, group_id: str, actor_user_id: str | None, event_type: str, title: str, message: str, payload: dict | None = None, exclude_user_id: str | None = None) -> None:
        try:
            self.notifications.notify_group(db, group_id=group_id, actor_user_id=actor_user_id, event_type=event_type, title=title, message=message, payload=payload, exclude_user_id=exclude_user_id)
        except Exception:
            return

    def _resolve_year_month(self, year: int | None = None, month: int | None = None) -> tuple[int, int]:
        now = datetime.utcnow()
        return int(year or now.year), int(month or now.month)

    def _ensure_monthly_obligation(
        self,
        db: Session,
        *,
        group_id: str,
        principal: SupabasePrincipal,
        player_id: str,
        user_id: str,
        player_name: str,
        year: int,
        month: int,
    ) -> dict:
        existing = self.repository.fetch_monthly_obligation(db, group_id=group_id, player_id=player_id, year=year, month=month)
        if existing:
            return existing
        group = self._group_or_404(db, group_id=group_id)
        amount = float(group.get('monthly_cost') or 0)
        if amount <= 0:
            raise HTTPException(status_code=422, detail='O grupo não possui mensalidade configurada.')
        due_day = int(group.get('payment_due_day') or 10)
        last_day = monthrange(year, month)[1]
        due_date = date(year, month, min(max(due_day, 1), last_day))
        obligation_id = self.repository.create_obligation(db, payload={
            'group_id': group_id,
            'user_id': user_id,
            'player_id': player_id,
            'match_id': None,
            'source_type': 'mensalidade',
            'title': f'Mensalidade {month:02d}/{year}',
            'description': 'Cobrança mensal gerada automaticamente.',
            'amount': amount,
            'currency': group['currency'],
            'status': 'aberta',
            'due_date': due_date,
            'competence_month': month,
            'competence_year': year,
            'created_by_user_id': principal.user_id,
        })
        return self.repository.fetch_obligation(db, group_id=group_id, obligation_id=obligation_id)

    def list_monthly_members(self, db: Session, principal: SupabasePrincipal, group_id: str, *, year: int | None = None, month: int | None = None) -> list[FinanceV2MonthlyMemberStatusModel]:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)
        resolved_year, resolved_month = self._resolve_year_month(year, month)
        today = datetime.utcnow().date()

        active_members = {m['player_id']: m for m in self.repository.list_active_monthly_members(db, group_id=group_id)}
        monthly_obligations = [
            o for o in self.repository.list_obligations(db, group_id=group_id)
            if (o.get('source_type') or '').lower() == 'mensalidade'
            and int(o.get('competence_year') or 0) == resolved_year
            and int(o.get('competence_month') or 0) == resolved_month
        ]

        ordered_player_ids: list[str] = []
        for member in active_members.values():
            if member.get('player_id') and member['player_id'] not in ordered_player_ids:
                ordered_player_ids.append(member['player_id'])
        for obligation in monthly_obligations:
            pid = obligation.get('player_id')
            if pid and pid not in ordered_player_ids:
                ordered_player_ids.append(pid)

        obligation_by_player = {o.get('player_id'): o for o in monthly_obligations if o.get('player_id')}
        items: list[FinanceV2MonthlyMemberStatusModel] = []

        for player_id in ordered_player_ids:
            member = active_members.get(player_id) or {}
            obligation = obligation_by_player.get(player_id)
            paid_entry = self.repository.fetch_paid_entry_for_obligation(db, group_id=group_id, obligation_id=obligation['obligation_id']) if obligation else None
            due_date = obligation.get('due_date') if obligation else None
            due_text = due_date.isoformat()[:10] if isinstance(due_date, (date, datetime)) else (str(due_date)[:10] if due_date else None)

            is_paid = paid_entry is not None
            is_overdue = False
            if not is_paid and due_text:
                try:
                    is_overdue = date.fromisoformat(due_text) < today
                except Exception:
                    is_overdue = False

            has_obligation = obligation is not None
            status = 'paid' if is_paid else ('overdue' if is_overdue else ('pending' if has_obligation else 'no_obligation'))
            items.append(FinanceV2MonthlyMemberStatusModel(
                user_id=(member.get('user_id') or (obligation.get('user_id') if obligation else None) or ''),
                player_id=player_id,
                player_name=member.get('display_name') or (obligation.get('player_name') if obligation else None) or 'Jogador',
                billing_type='monthly',
                amount=float(obligation.get('amount') if obligation else group.get('monthly_cost') or 0),
                paid=is_paid,
                due_date=due_text,
                confirmed_by_user_id=paid_entry.get('created_by_user_id') if paid_entry else None,
                confirmed_by_user_name=None,
                can_unmark=bool(paid_entry and paid_entry.get('created_by_user_id') == principal.user_id),
                obligation_id=obligation.get('obligation_id') if obligation else None,
                entry_id=paid_entry.get('entry_id') if paid_entry else None,
                status=status,
                display_status=status,
                is_overdue=is_overdue,
                automation_source='manual_or_auto',
                avatar_url=member.get('avatar_url') or (obligation.get('player_avatar_url') if obligation else None),
            ))

        return items

    def get_billing_members(self, db: Session, principal: SupabasePrincipal, group_id: str, *, year: int | None = None, month: int | None = None) -> FinanceV2BillingMembersModel:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)
        resolved_year, resolved_month = self._resolve_year_month(year, month)
        monthly_members = self.list_monthly_members(db, principal, group_id, year=resolved_year, month=resolved_month)
        obligations = self.repository.list_obligations(db, group_id=group_id)
        entries = self.repository.list_entries(db, group_id=group_id)

        single_members: list[FinanceV2SingleMemberStatusModel] = []
        by_player: dict[str, dict] = {}

        def ensure_player(payload: dict, *, name_key: str = 'player_name') -> dict | None:
            player_id = payload.get('player_id')
            if not player_id:
                return None
            if player_id not in by_player:
                by_player[player_id] = {
                    'user_id': payload.get('user_id') or '',
                    'player_id': player_id,
                    'player_name': payload.get(name_key) or payload.get('player_name') or 'Jogador',
                    'avatar_url': payload.get('player_avatar_url') or payload.get('avatar_url') or payload.get('user_avatar_url'),
                    'month_paid': 0.0,
                    'month_pending': 0.0,
                }
            return by_player[player_id]

        for item in obligations:
            try:
                if (item.get('source_type') or '').lower() != 'avulso_partida':
                    continue
                if not self._month_matches(item, year=resolved_year, month=resolved_month):
                    continue
                bucket = ensure_player(item)
                if bucket is None:
                    continue
                bucket['month_pending'] += float(item.get('amount') or 0)
            except Exception:
                continue

        for item in entries:
            try:
                public_type = self._source_to_public_entry_type(item.get('obligation_source_type'), item.get('category'), item.get('entry_type'))
                if public_type != 'single' or not self._month_matches(item, year=resolved_year, month=resolved_month):
                    continue
                bucket = ensure_player(item)
                if bucket is None:
                    continue
                bucket['month_paid'] += float(item.get('amount') or 0)
            except Exception:
                continue

        for payload in by_player.values():
            try:
                month_pending = max(float(payload.get('month_pending') or 0) - float(payload.get('month_paid') or 0), 0.0)
                month_paid = float(payload.get('month_paid') or 0)
                if month_pending <= 0 and month_paid <= 0:
                    continue
                single_members.append(FinanceV2SingleMemberStatusModel(
                    user_id=payload['user_id'],
                    player_id=payload['player_id'],
                    player_name=payload['player_name'],
                    billing_type='single',
                    month_paid=month_paid,
                    month_pending=month_pending,
                    financial_status='inadimplente' if month_pending > 0 else 'adimplente',
                    avatar_url=payload.get('avatar_url'),
                ))
            except Exception:
                continue

        return FinanceV2BillingMembersModel(
            group_id=group_id,
            currency=group.get('currency') or 'BRL',
            year=resolved_year,
            month=resolved_month,
            monthly_members=monthly_members,
            single_members=single_members,
        )

    def get_automation_status(self, db: Session, principal: SupabasePrincipal, group_id: str) -> FinanceV2AutomationStatusModel:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)
        year, month = self._resolve_year_month()
        monthly_members = self.list_monthly_members(db, principal, group_id, year=year, month=month)
        real_monthly_obligations = [
            m for m in monthly_members
            if m.obligation_id and (m.status or '').lower() not in {'no_obligation', 'excluida', 'deleted'}
        ]
        ready = self._is_hybrid_group(group) and int(group.get('payment_due_day') or 0) > 0 and float(group.get('monthly_cost') or 0) > 0
        return FinanceV2AutomationStatusModel(
            group_id=group_id,
            automation_ready=ready,
            automation_enabled=ready,
            reference_year=year,
            reference_month=month,
            created_now=0,
            skipped_now=0,
            monthly_members_count=len(monthly_members),
            generated_entries_count=len(real_monthly_obligations),
            paid_entries_count=len([m for m in real_monthly_obligations if m.paid]),
            pending_entries_count=len([m for m in real_monthly_obligations if not m.paid and not m.is_overdue]),
            overdue_entries_count=len([m for m in real_monthly_obligations if m.is_overdue]),
            due_day=group.get('payment_due_day'),
            monthly_cost=float(group.get('monthly_cost') or 0),
            message='Automação pronta para gerar e acompanhar mensalidades reais do mês atual.' if ready else 'Configure mensalidade e dia de vencimento para habilitar a automação financeira.',
        )

    def update_settings(self, db: Session, principal: SupabasePrincipal, group_id: str, payload: FinanceV2SettingsRequest) -> dict:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)
        updates = {}
        if payload.payment_method is not None:
            updates['payment_method'] = payload.payment_method
        if payload.payment_key is not None:
            updates['payment_key'] = payload.payment_key
        if payload.due_day is not None:
            if not self._is_hybrid_group(group):
                raise HTTPException(status_code=422, detail='Data limite só pode ser configurada em grupos híbridos.')
            updates['payment_due_day'] = int(payload.due_day)
        if updates:
            db.execute(text("""
                update public.groups
                set payment_method = coalesce(:payment_method, payment_method),
                    payment_key = coalesce(:payment_key, payment_key),
                    payment_due_day = coalesce(:payment_due_day, payment_due_day),
                    updated_at = now()
                where id = cast(:group_id as uuid)
            """), {
                'group_id': group_id,
                'payment_method': updates.get('payment_method'),
                'payment_key': updates.get('payment_key'),
                'payment_due_day': updates.get('payment_due_day'),
            })
            db.commit()
            self._invalidate_group_cache(group_id=group_id)
        refreshed = self._group_or_404(db, group_id=group_id)
        return {
            'group_id': group_id,
            'payment_method': refreshed.get('payment_method'),
            'payment_key': refreshed.get('payment_key'),
            'due_day': refreshed.get('payment_due_day'),
            'message': 'Configuração financeira atualizada com sucesso',
        }

    def mark_monthly_member_paid(self, db: Session, principal: SupabasePrincipal, group_id: str, player_id: str, *, year: int | None = None, month: int | None = None) -> FinanceV2EntryModel:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        resolved_year, resolved_month = self._resolve_year_month(year, month)
        obligation = self.repository.fetch_monthly_obligation(db, group_id=group_id, player_id=player_id, year=resolved_year, month=resolved_month)
        if not obligation:
            raise HTTPException(status_code=404, detail='Mensalidade do período selecionado não encontrada. Gere a mensalidade antes de marcar como paga.')
        paid_entry = self.repository.fetch_paid_entry_for_obligation(db, group_id=group_id, obligation_id=obligation['obligation_id'])
        if paid_entry:
            return self._to_entry_model(paid_entry, current_user_id=principal.user_id)
        return self.create_entry(db, principal, group_id, FinanceV2CreateEntryRequest(
            obligation_id=obligation['obligation_id'],
            entry_type='inflow',
            category='mensalidade',
            amount=float(obligation.get('amount') or 0),
            notes=f'Pagamento mensal {resolved_month:02d}/{resolved_year} confirmado por {principal.user_id}',
        ))

    def unmark_monthly_member_paid(self, db: Session, principal: SupabasePrincipal, group_id: str, player_id: str, *, year: int | None = None, month: int | None = None) -> dict:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        resolved_year, resolved_month = self._resolve_year_month(year, month)
        obligation = self.repository.fetch_monthly_obligation(db, group_id=group_id, player_id=player_id, year=resolved_year, month=resolved_month)
        if not obligation:
            raise HTTPException(status_code=404, detail='Mensalidade do período selecionado não encontrada.')
        paid_entry = self.repository.fetch_paid_entry_for_obligation(db, group_id=group_id, obligation_id=obligation['obligation_id'])
        if not paid_entry:
            raise HTTPException(status_code=404, detail='Pagamento não encontrado para esta mensalidade.')
        self.delete_entry(db, principal, group_id, paid_entry['entry_id'])
        return {'status': 'ok'}

    def mark_paid(self, db: Session, principal: SupabasePrincipal, group_id: str, reference_id: str, payload: FinanceV2MarkPaidRequest) -> FinanceV2EntryModel:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        existing_entry = self.repository.fetch_entry(db, group_id=group_id, entry_id=reference_id)
        if existing_entry:
            return self._to_entry_model(existing_entry, current_user_id=principal.user_id)
        obligation = self.repository.fetch_obligation_by_reference(db, group_id=group_id, reference_id=reference_id)
        if not obligation:
            raise HTTPException(status_code=404, detail='Referência financeira não encontrada.')
        amount = float(payload.amount or obligation.get('amount') or 0)
        category = obligation.get('source_type') or 'manual'
        return self.create_entry(db, principal, group_id, FinanceV2CreateEntryRequest(
            obligation_id=obligation['obligation_id'],
            entry_type='inflow',
            category=category,
            amount=amount,
            notes=payload.notes,
        ))

    def unmark_paid(self, db: Session, principal: SupabasePrincipal, group_id: str, entry_id: str) -> dict:
        self.delete_entry(db, principal, group_id, entry_id)
        return {'status': 'ok'}

    def get_summary(self, db: Session, principal: SupabasePrincipal, group_id: str, *, year: int | None = None, month: int | None = None) -> FinanceV2SummaryModel:
        self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=principal.user_id)
        now = datetime.utcnow()
        resolved_year = int(year or now.year)
        resolved_month = int(month or now.month)
        key = self._cache_key('summary', group_id, principal.user_id, year=resolved_year, month=resolved_month)

        def _load():
            group = self._group_or_404(db, group_id=group_id)
            entries = self.repository.list_entries(db, group_id=group_id)
            obligations = self.repository.list_obligations(db, group_id=group_id)
            data = self._build_summary_payload(group=group, entries=entries, obligations=obligations, year=resolved_year, month=resolved_month)
            return FinanceV2SummaryModel(**data)

        return app_cache.get_or_set(key, _load, 30)

    def list_obligations(self, db: Session, principal: SupabasePrincipal, group_id: str) -> list[FinanceV2ObligationModel]:
        self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=principal.user_id)
        key = self._cache_key('obligations', group_id, principal.user_id)
        return app_cache.get_or_set(key, lambda: [FinanceV2ObligationModel(**item) for item in self.repository.list_obligations(db, group_id=group_id)], 20)

    def list_entries(self, db: Session, principal: SupabasePrincipal, group_id: str) -> list[FinanceV2EntryModel]:
        self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=principal.user_id)
        key = self._cache_key('entries', group_id, principal.user_id)
        return app_cache.get_or_set(key, lambda: [self._to_entry_model(item, current_user_id=principal.user_id) for item in self.repository.list_entries(db, group_id=group_id)], 20)

    def list_ledger(self, db: Session, principal: SupabasePrincipal, group_id: str) -> list[FinanceV2LedgerModel]:
        self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=principal.user_id)
        key = self._cache_key('ledger', group_id, principal.user_id)
        return app_cache.get_or_set(key, lambda: [FinanceV2LedgerModel(**item) for item in self.repository.list_ledger(db, group_id=group_id)], 20)

    def generate_monthly_obligations(self, db: Session, principal: SupabasePrincipal, group_id: str, payload: FinanceV2MonthlyGenerateRequest) -> FinanceV2MonthlyGenerateResult:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)
        if not self._is_hybrid_group(group):
            raise HTTPException(status_code=422, detail='Geração de mensalidades só está disponível em grupos híbridos.')
        amount = float(group.get('monthly_cost') or 0)
        if amount <= 0:
            raise HTTPException(status_code=422, detail='O grupo não possui mensalidade configurada.')
        due_day = int(group.get('payment_due_day') or 10)
        last_day = monthrange(payload.year, payload.month)[1]
        due_date = date(payload.year, payload.month, min(max(due_day, 1), last_day))
        generated = 0
        for member in self.repository.list_active_monthly_members(db, group_id=group_id):
            if self.repository.obligation_exists_for_monthly(db, group_id=group_id, player_id=member['player_id'], month=payload.month, year=payload.year):
                continue
            obligation_id = self.repository.create_obligation(db, payload={
                'group_id': group_id,
                'user_id': member['user_id'],
                'player_id': member['player_id'],
                'match_id': None,
                'source_type': 'mensalidade',
                'title': f"Mensalidade {payload.month:02d}/{payload.year}",
                'description': f'Cobrança mensal gerada manualmente por {identity.get("display_name") or "admin"}.',
                'amount': amount,
                'currency': group['currency'],
                'status': 'aberta',
                'due_date': due_date,
                'competence_month': payload.month,
                'competence_year': payload.year,
                'created_by_user_id': principal.user_id,
            })
            generated += 1
            self._notify_group(
                db,
                group_id=group_id,
                actor_user_id=principal.user_id,
                event_type='finance.monthly.generated',
                title='Mensalidade gerada',
                message=f"Mensalidade criada para {member['display_name']}.",
                payload={'obligation_id': obligation_id, 'player_id': member['player_id']},
            )
        db.commit()
        self._invalidate_group_cache(group_id=group_id)
        return FinanceV2MonthlyGenerateResult(month=payload.month, year=payload.year, generated_obligations=generated)

    def generate_match_obligations(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> FinanceV2GenerateMatchResult:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)
        match = self.repository.fetch_match_context(db, group_id=group_id, match_id=match_id)
        if not match:
            raise HTTPException(status_code=404, detail='Partida não encontrada.')
        single_cost = float(group.get('single_cost') or 0)
        venue_cost = float(group.get('venue_cost') or 0)
        generated_obligations = 0
        generated_entries = 0

        for candidate in self.repository.list_match_charge_candidates(db, match_id=match_id):
            if self.repository.obligation_exists_for_match_player(db, match_id=match_id, player_id=candidate['player_id']):
                continue
            self.repository.create_obligation(db, payload={
                'group_id': group_id,
                'user_id': candidate['user_id'],
                'player_id': candidate['player_id'],
                'match_id': match_id,
                'source_type': 'avulso_partida',
                'title': f"Avulso - {match['title']}",
                'description': 'Cobrança avulsa da partida para confirmado com chegada.',
                'amount': single_cost,
                'currency': group['currency'],
                'status': 'aberta',
                'due_date': match.get('starts_at').date() if isinstance(match.get('starts_at'), datetime) else None,
                'competence_month': None,
                'competence_year': None,
                'created_by_user_id': principal.user_id,
            })
            generated_obligations += 1

        if venue_cost > 0 and not self.repository.entry_exists_for_match_court(db, match_id=match_id):
            entry_id = self.repository.create_entry(db, payload={
                'group_id': group_id,
                'obligation_id': None,
                'user_id': None,
                'player_id': None,
                'match_id': match_id,
                'entry_type': 'outflow',
                'category': 'quadra',
                'amount': venue_cost,
                'currency': group['currency'],
                'paid_at': datetime.utcnow(),
                'notes': 'Custo da quadra lançado automaticamente na geração da partida.',
                'created_by_user_id': principal.user_id,
            })
            self.repository.create_ledger(db, payload={
                'group_id': group_id,
                'obligation_id': None,
                'entry_id': entry_id,
                'movement_type': 'saida',
                'direction': 'debit',
                'amount': venue_cost,
                'balance_impact': -venue_cost,
                'description': f"Custo da quadra - {match['title']}",
                'reference_date': match.get('starts_at') or datetime.utcnow(),
            })
            generated_entries += 1

        db.commit()
        self._invalidate_group_cache(group_id=group_id)
        self._notify_group(
            db,
            group_id=group_id,
            actor_user_id=principal.user_id,
            event_type='finance.match.generated',
            title='Financeiro da partida atualizado',
            message='Cobranças e custo de quadra foram processados.',
            payload={'match_id': match_id, 'generated_obligations': generated_obligations, 'generated_entries': generated_entries},
        )
        return FinanceV2GenerateMatchResult(match_id=match_id, generated_obligations=generated_obligations, generated_entries=generated_entries)

    def create_manual_transaction(self, db: Session, principal: SupabasePrincipal, group_id: str, payload: FinanceV2ManualTransactionRequest) -> FinanceV2EntryModel:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)

        transaction_type = (payload.transaction_type or 'manual').strip().lower()
        expense_types = {'venue', 'quadra', 'venue_cost', 'extra_expense', 'extra-expense', 'manual', 'debit_adjustment', 'ajuste_debito'}
        income_types = {'credit_adjustment', 'ajuste_credito'}

        if transaction_type in {'venue', 'quadra', 'venue_cost'}:
            category = 'venue'
            description = (payload.description or '').strip() or 'Valor do local'
            public_type = 'venue'
            entry_type = 'outflow'
        elif transaction_type in {'extra_expense', 'extra-expense'}:
            category = 'extra_expense'
            description = (payload.description or '').strip() or 'Outras despesas'
            public_type = 'extra_expense'
            entry_type = 'outflow'
        elif transaction_type in income_types:
            category = 'manual'
            description = (payload.description or '').strip() or 'Ajuste de crédito'
            public_type = 'manual'
            entry_type = 'inflow'
        else:
            category = 'manual'
            description = (payload.description or '').strip() or 'Outras despesas'
            public_type = 'extra_expense' if transaction_type in expense_types else 'manual'
            entry_type = 'outflow' if transaction_type in expense_types else 'inflow'

        entry_id = self.repository.create_entry(db, payload={
            'group_id': group_id,
            'obligation_id': None,
            'user_id': payload.user_id,
            'player_id': payload.player_id,
            'match_id': payload.match_id,
            'entry_type': entry_type,
            'category': category,
            'amount': float(payload.amount),
            'currency': group['currency'],
            'paid_at': datetime.utcnow(),
            'notes': description if not payload.notes else payload.notes,
            'created_by_user_id': principal.user_id,
        })

        direction = 'credit' if entry_type == 'inflow' else 'debit'
        balance_impact = float(payload.amount) if direction == 'credit' else -float(payload.amount)
        self.repository.create_ledger(db, payload={
            'group_id': group_id,
            'obligation_id': None,
            'entry_id': entry_id,
            'movement_type': 'entrada' if direction == 'credit' else 'saida',
            'direction': direction,
            'amount': float(payload.amount),
            'balance_impact': balance_impact,
            'description': description,
            'reference_date': datetime.utcnow(),
        })

        db.commit()
        self._invalidate_group_cache(group_id=group_id)
        enriched = self.repository.list_entries(db, group_id=group_id)
        current = next((item for item in enriched if item['entry_id'] == entry_id), None)
        if current is None:
            current = self.repository.fetch_entry(db, group_id=group_id, entry_id=entry_id) or {
                'entry_id': entry_id,
                'id': entry_id,
                'group_id': group_id,
                'user_id': payload.user_id,
                'player_id': payload.player_id,
                'match_id': payload.match_id,
                'entry_type': entry_type,
                'category': category,
                'amount': float(payload.amount),
                'currency': group['currency'],
                'notes': payload.notes or description,
                'created_by_user_id': principal.user_id,
                'created_at': datetime.utcnow(),
            }
            current['user_name'] = description
            current['player_name'] = description
            current['obligation_source_type'] = public_type
        else:
            current['obligation_source_type'] = public_type
            if not current.get('user_name') or current.get('user_name') == 'Jogador':
                current['user_name'] = description
            if not current.get('player_name') or current.get('player_name') == 'Jogador':
                current['player_name'] = description
            if not current.get('guest_name'):
                current['guest_name'] = description

        self._notify_group(
            db,
            group_id=group_id,
            actor_user_id=principal.user_id,
            event_type='finance.entry.created',
            title='Financeiro atualizado',
            message='Uma movimentação financeira foi registrada.',
            payload={'entry_id': entry_id, 'entry_type': public_type, 'amount': float(payload.amount)},
        )
        return self._to_entry_model(current, current_user_id=principal.user_id)

    def create_entry(self, db: Session, principal: SupabasePrincipal, group_id: str, payload: FinanceV2CreateEntryRequest) -> FinanceV2EntryModel:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        group = self._group_or_404(db, group_id=group_id)
        obligation = None
        if payload.obligation_id:
            obligation = self.repository.fetch_obligation(db, group_id=group_id, obligation_id=payload.obligation_id)
            if not obligation:
                raise HTTPException(status_code=404, detail='Obrigação financeira não encontrada.')
            if obligation.get('status') == 'paga' and payload.entry_type == 'inflow':
                raise HTTPException(status_code=409, detail='Esta obrigação já foi marcada como paga.')

        entry_id = self.repository.create_entry(db, payload={
            'group_id': group_id,
            'obligation_id': payload.obligation_id,
            'user_id': obligation.get('user_id') if obligation else None,
            'player_id': obligation.get('player_id') if obligation else None,
            'match_id': obligation.get('match_id') if obligation else None,
            'entry_type': payload.entry_type,
            'category': payload.category,
            'amount': float(payload.amount),
            'currency': group['currency'],
            'paid_at': datetime.utcnow(),
            'notes': payload.notes,
            'created_by_user_id': principal.user_id,
        })

        direction = 'credit' if payload.entry_type == 'inflow' else 'debit'
        balance_impact = float(payload.amount) if direction == 'credit' else -float(payload.amount)
        self.repository.create_ledger(db, payload={
            'group_id': group_id,
            'obligation_id': payload.obligation_id,
            'entry_id': entry_id,
            'movement_type': 'entrada' if direction == 'credit' else 'saida',
            'direction': direction,
            'amount': float(payload.amount),
            'balance_impact': balance_impact,
            'description': payload.notes or payload.category,
            'reference_date': datetime.utcnow(),
        })
        if obligation and payload.entry_type == 'inflow':
            self.repository.mark_obligation_paid(db, obligation_id=payload.obligation_id)
        db.commit()
        self._invalidate_group_cache(group_id=group_id)
        entry = self.repository.fetch_entry(db, group_id=group_id, entry_id=entry_id)
        enriched = self.repository.list_entries(db, group_id=group_id)
        current = next((item for item in enriched if item['entry_id'] == entry_id), entry)
        self._notify_group(
            db,
            group_id=group_id,
            actor_user_id=principal.user_id,
            event_type='finance.entry.created',
            title='Financeiro atualizado',
            message='Uma movimentação financeira foi registrada.',
            payload={'entry_id': entry_id, 'entry_type': payload.entry_type, 'amount': float(payload.amount)},
        )
        return self._to_entry_model(current, current_user_id=principal.user_id)

    def delete_entry(self, db: Session, principal: SupabasePrincipal, group_id: str, entry_id: str) -> None:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        entry = self.repository.fetch_entry(db, group_id=group_id, entry_id=entry_id)
        if not entry:
            raise HTTPException(status_code=404, detail='Lançamento não encontrado.')
        if entry.get('created_by_user_id') and entry.get('created_by_user_id') != principal.user_id:
            raise HTTPException(status_code=403, detail='Somente quem marcou o pagamento pode desfazer.')
        obligation_id = entry.get('obligation_id')
        self.repository.delete_entry(db, group_id=group_id, entry_id=entry_id)
        if obligation_id:
            self.repository.update_obligation_status(db, obligation_id=obligation_id, status='aberta')
        db.commit()
        self._invalidate_group_cache(group_id=group_id)
        self._notify_group(
            db,
            group_id=group_id,
            actor_user_id=principal.user_id,
            event_type='finance.entry.deleted',
            title='Pagamento desfeito',
            message='Uma movimentação financeira foi removida.',
            payload={'entry_id': entry_id, 'obligation_id': obligation_id},
        )

    def soft_delete_obligation(self, db: Session, principal: SupabasePrincipal, group_id: str, obligation_id: str) -> dict:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        obligation = self.repository.fetch_obligation(db, group_id=group_id, obligation_id=obligation_id)
        if not obligation:
            raise HTTPException(status_code=404, detail='Obrigação não encontrada.')
        if obligation.get('status') == 'excluida':
            raise HTTPException(status_code=409, detail='Esta obrigação já foi excluída.')
        paid_entry = self.repository.fetch_paid_entry_for_obligation(db, group_id=group_id, obligation_id=obligation_id)
        if paid_entry:
            raise HTTPException(status_code=409, detail='Não é possível excluir obrigação com pagamento vinculado. Desfaça o pagamento primeiro.')
        db.execute(text("""
            update public.finance_obligations_v2
            set status = 'excluida', deleted_at = now(), deleted_by_user_id = cast(:uid as uuid), updated_at = now()
            where id = cast(:oid as uuid) and group_id = cast(:gid as uuid)
        """), {'oid': obligation_id, 'gid': group_id, 'uid': principal.user_id})
        db.execute(text("delete from public.finance_ledger_v2 where obligation_id = cast(:oid as uuid) and group_id = cast(:gid as uuid)"), {'oid': obligation_id, 'gid': group_id})
        db.execute(text("delete from public.finance_entries_v2 where obligation_id = cast(:oid as uuid) and group_id = cast(:gid as uuid)"), {'oid': obligation_id, 'gid': group_id})
        db.commit()
        self._invalidate_group_cache(group_id=group_id)
        return {'status': 'deleted', 'obligation_id': obligation_id, 'deleted_by': principal.user_id}

    def list_deleted_obligations(self, db: Session, principal: SupabasePrincipal, group_id: str, *, year: int | None = None, month: int | None = None) -> list:
        self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=principal.user_id)
        resolved_year, resolved_month = self._resolve_year_month(year, month)
        try:
            rows = db.execute(text("""
                select o.id::text as obligation_id, o.title, o.amount::float, o.currency,
                       o.competence_month, o.competence_year, o.source_type, o.deleted_at,
                       o.deleted_by_user_id::text,
                       coalesce(nullif(trim(p_del.display_name), ''), nullif(trim(p_del.full_name), ''), 'Admin') as deleted_by_name,
                       coalesce(nullif(trim(p.display_name), ''), nullif(trim(p.full_name), ''), nullif(trim(o.title), ''), 'Jogador') as player_name
                from public.finance_obligations_v2 o
                left join public.players p on p.id = o.player_id
                left join public.players p_del on p_del.user_id = o.deleted_by_user_id
                where o.group_id = cast(:gid as uuid)
                  and (o.status = 'excluida' or o.deleted_at is not null)
                  and (:year is null or o.competence_year = :year)
                  and (:month is null or o.competence_month = :month)
                order by o.deleted_at desc nulls last
            """), {'gid': group_id, 'year': resolved_year, 'month': resolved_month}).mappings().all()
            return [dict(r) for r in rows]
        except Exception:
            # A seção de auditoria não pode derrubar o financeiro em produção.
            # O erro deve ser investigado via logs de aplicação/DB sem impactar a UX.
            return []
