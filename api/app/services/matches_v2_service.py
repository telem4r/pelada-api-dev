from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.core.cache import app_cache
from app.core.logging import configure_logging, log_event
from app.repositories.matches_v2 import MatchesV2Repository
from app.services.notifications_v2_service import NotificationsV2Service
from app.services.avatar_resolver import resolve_avatar, resolve_avatars
from app.schemas.matches_v2 import (
    MatchCreateV2Request,
    MatchDrawBaseItemV2Model,
    MatchDrawBaseV2Model,
    MatchDrawGenerateV2Request,
    MatchDrawResultV2Model,
    MatchDrawTeamItemV2Model,
    MatchDrawTeamV2Model,
    MatchEventCreateV2Request,
    MatchEventV2Model,
    MatchGameFlowV2Model,
    MatchPlayerStatV2Model,
    MatchStatsSummaryV2Model,
    MatchGuestCreateV2Request,
    MatchGuestV2Model,
    MatchOperationLocksV2Request,
    MatchPostStatsV2Request,
    MatchUpdateV2Request,
    MatchParticipantV2Model,
    MatchPresenceUpsertV2Request,
    MatchPresenceV2Model,
    MatchScoreTeamV2Model,
    MatchSummaryV2Model,
)


logger = configure_logging()


class MatchesV2Service:
    def __init__(self, repository: MatchesV2Repository | None = None) -> None:
        self.repository = repository or MatchesV2Repository()
        self.notifications = NotificationsV2Service()

    def _lock_match_presence_scope(self, db: Session, *, match_id: str) -> None:
        self.repository.lock_match(db, match_id=match_id)

    def _presence_response_from_match(self, db: Session, *, match: dict) -> MatchPresenceV2Model:
        return self._presence_snapshot(
            db,
            match_id=match['id'],
            line_slots=int(match['line_slots']),
            goalkeeper_slots=int(match['goalkeeper_slots']),
        )

    def _log_presence_event(self, event_type: str, **fields) -> None:
        log_event(logger, event_type, **fields)

    def _cache_key(self, kind: str, group_id: str, match_id: str | None = None, user_id: str | None = None) -> str:
        base = f"matches_v2:{kind}:group:{group_id}"
        if match_id:
            base += f":match:{match_id}"
        if user_id:
            base += f":user:{user_id}"
        return base

    def _invalidate_group_cache(self, *, group_id: str, match_id: str | None = None) -> None:
        app_cache.invalidate_prefix(f"matches_v2:list:group:{group_id}")
        app_cache.invalidate_prefix(f"matches_v2:stats:group:{group_id}")
        app_cache.invalidate_prefix(f"matches_v2:game_flow:group:{group_id}")
        if match_id:
            app_cache.invalidate_prefix(f"matches_v2:match:group:{group_id}:match:{match_id}")
            app_cache.invalidate_prefix(f"matches_v2:presence:group:{group_id}:match:{match_id}")
            app_cache.invalidate_prefix(f"matches_v2:guests:group:{group_id}:match:{match_id}")
            app_cache.invalidate_prefix(f"matches_v2:draw_base:group:{group_id}:match:{match_id}")
            app_cache.invalidate_prefix(f"matches_v2:draw_result:group:{group_id}:match:{match_id}")
            app_cache.invalidate_prefix(f"matches_v2:game_flow:group:{group_id}:match:{match_id}")
            app_cache.invalidate_prefix(f"matches_v2:stats:group:{group_id}:match:{match_id}")

    def _identity_or_404(self, db: Session, principal: SupabasePrincipal) -> dict:
        identity = self.repository.fetch_foundation_identity(db, user_id=principal.user_id)
        if not identity:
            raise HTTPException(status_code=404, detail='Sessão BoraFut não bootstrapada para este utilizador.')
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

    def _apply_group_defaults_to_match(self, match: dict, group: dict | None) -> dict:
        if not match:
            return match
        if not group:
            return match
        normalized = dict(match)
        fallback_fields = (
            'city',
            'payment_method',
            'payment_key',
            'single_waitlist_release_days',
            'modality',
            'gender_type',
        )
        for field in fallback_fields:
            if normalized.get(field) in (None, ''):
                group_value = group.get(field)
                if group_value not in (None, ''):
                    normalized[field] = group_value
        return normalized

    def _match_or_404(self, db: Session, *, group_id: str, match_id: str) -> dict:
        match = self.repository.fetch_match(db, group_id=group_id, match_id=match_id)
        if not match:
            raise HTTPException(status_code=404, detail='Partida não encontrada.')
        group = self.repository.fetch_group(db, group_id=group_id)
        return self._apply_group_defaults_to_match(match, group)

    def _ensure_roster_open(self, match: dict, *, action: str) -> None:
        if bool(match.get('draw_locked')):
            raise HTTPException(status_code=409, detail='O sorteio está fixado. Libere o sorteio para ' + action + '.')
        if bool(match.get('roster_locked')):
            raise HTTPException(status_code=409, detail='O elenco da partida está fechado. Libere o elenco para ' + action + '.')
        if match.get('status') in {'in_progress', 'finished'}:
            raise HTTPException(status_code=409, detail='A partida já iniciou. Não é possível ' + action + ' neste momento.')

    def _ensure_draw_not_locked(self, match: dict, *, action: str) -> None:
        if bool(match.get('draw_locked')):
            raise HTTPException(status_code=409, detail='O sorteio está fixado. Libere o sorteio para ' + action + '.')
        if match.get('status') in {'finished'}:
            raise HTTPException(status_code=409, detail='A partida já foi finalizada. Não é possível ' + action + ' neste momento.')
        ends_at = match.get('ends_at')
        if ends_at is not None:
            now = datetime.now(timezone.utc)
            if hasattr(ends_at, 'tzinfo') and ends_at.tzinfo is None:
                ends_at = ends_at.replace(tzinfo=timezone.utc)
            if now > ends_at:
                raise HTTPException(status_code=409, detail='O horário da partida já terminou. Não é possível ' + action + ' neste momento.')


    def _notify_group(self, db: Session, *, group_id: str, actor_user_id: str | None, event_type: str, title: str, message: str, payload: dict | None = None, exclude_user_id: str | None = None) -> None:
        try:
            self.notifications.notify_group(db, group_id=group_id, actor_user_id=actor_user_id, event_type=event_type, title=title, message=message, payload=payload, exclude_user_id=exclude_user_id)
        except Exception:
            return

    def _validate_match_payload(self, payload) -> None:
        starts_at = getattr(payload, 'starts_at', None)
        ends_at = getattr(payload, 'ends_at', None)
        line_slots = getattr(payload, 'line_slots', None)
        goalkeeper_slots = getattr(payload, 'goalkeeper_slots', None)

        if starts_at is not None and ends_at is not None and ends_at <= starts_at:
            raise HTTPException(status_code=422, detail='A data/hora final deve ser maior que a data/hora inicial.')

        if line_slots is not None and goalkeeper_slots is not None and (line_slots + goalkeeper_slots) <= 0:
            raise HTTPException(status_code=422, detail='Defina pelo menos uma vaga para a partida.')


    def _is_monthly_adimplente(self, db: Session, *, group_id: str, user_id: str, starts_at: datetime | None) -> bool:
        ref = starts_at or datetime.utcnow()
        return not self.repository.has_open_monthly_obligation(db, group_id=group_id, user_id=user_id, year=ref.year, month=ref.month)

    def _within_waitlist_release_window(self, *, group: dict | None, match: dict) -> bool:
        days = int((group or {}).get('single_waitlist_release_days') or 0)
        if days <= 0:
            return False
        starts_at = match.get('starts_at')
        if not starts_at:
            return False
        now = datetime.now(starts_at.tzinfo) if getattr(starts_at, 'tzinfo', None) is not None else datetime.utcnow()
        delta_days = (starts_at - now).total_seconds() / 86400.0
        return delta_days <= days

    def _resolve_presence_status(self, db: Session, *, group: dict | None, membership: dict, match: dict, presence: MatchPresenceV2Model, position: str) -> tuple[str, int]:
        role = (membership.get('role') or '').strip().lower()
        if role in {'owner', 'admin'}:
            return self._next_status_and_queue(presence, position=position)
        if (group or {}).get('group_type') == 'hibrido':
            billing_type = (membership.get('billing_type') or '').strip().lower()
            is_monthly = billing_type == 'mensalista'
            is_adimplente = self._is_monthly_adimplente(db, group_id=match['group_id'], user_id=membership['user_id'], starts_at=match.get('starts_at')) if is_monthly else False
            if is_monthly and is_adimplente:
                return self._next_status_and_queue(presence, position=position)
            waiting_order = (presence.waiting_goalkeeper_count + 1) if position == 'goleiro' else (presence.waiting_line_count + 1)
            return 'espera', waiting_order
        return self._next_status_and_queue(presence, position=position)

    def _presence_snapshot(self, db: Session, *, match_id: str, line_slots: int, goalkeeper_slots: int) -> MatchPresenceV2Model:
        rows = self.repository.list_presence(db, match_id=match_id)
        confirmed: list[MatchParticipantV2Model] = []
        waiting: list[MatchParticipantV2Model] = []
        draw_base: list[MatchParticipantV2Model] = []

        for row in rows:
            row_status = row.get('status') or 'espera'
            row_kind = row.get('kind') or 'member'
            row_position = row.get('position') or 'linha'
            requires_approval = False
            if row_kind == 'member' and row_status == 'espera' and (row.get('billing_type') or '').strip().lower() == 'mensalista':
                match_group_id = row.get('group_id')
                match_starts_at = row.get('match_starts_at')
                row_user_id = row.get('user_id')
                if match_group_id and row_user_id:
                    requires_approval = not self._is_monthly_adimplente(
                        db,
                        group_id=match_group_id,
                        user_id=row_user_id,
                        starts_at=match_starts_at,
                    )
            item = MatchParticipantV2Model(
                participant_id=row.get('participant_id'),
                player_id=row.get('player_id'),
                user_id=row.get('user_id'),
                guest_id=row.get('guest_id'),
                kind=row_kind,
                name=row.get('name') or 'Jogador',
                avatar_url=resolve_avatar(row.get('avatar_url')),
                position=row_position,
                status=row_status,
                queue_order=int(row.get('queue_order') or 0),
                is_paid=bool(row.get('is_paid')),
                has_arrived=bool(row.get('has_arrived')),
                approved_by_user_id=row.get('approved_by_user_id'),
                approved_by_user_name=row.get('approved_by_user_name'),
                requires_approval=requires_approval,
                can_play_draw=(row_status == 'confirmado' and bool(row.get('has_arrived'))),
                billing_type=row.get('billing_type'),
            )
            if item.status == 'confirmado':
                confirmed.append(item)
                if item.has_arrived:
                    draw_base.append(item)
            else:
                waiting.append(item)

        return MatchPresenceV2Model(
            match_id=match_id,
            line_slots=line_slots,
            goalkeeper_slots=goalkeeper_slots,
            confirmed=confirmed,
            waiting=waiting,
            confirmed_line_count=sum(1 for item in confirmed if item.position == 'linha'),
            confirmed_goalkeeper_count=sum(1 for item in confirmed if item.position == 'goleiro'),
            waiting_line_count=sum(1 for item in waiting if item.position == 'linha'),
            waiting_goalkeeper_count=sum(1 for item in waiting if item.position == 'goleiro'),
            arrived_count=sum(1 for item in confirmed if item.has_arrived),
            draw_eligible_count=len(draw_base),
        )

    def _next_status_and_queue(self, presence: MatchPresenceV2Model, *, position: str) -> tuple[str, int]:
        if position == 'goleiro':
            confirmed_count = presence.confirmed_goalkeeper_count
            slot_limit = presence.goalkeeper_slots
            waiting_count = presence.waiting_goalkeeper_count
        else:
            confirmed_count = presence.confirmed_line_count
            slot_limit = presence.line_slots
            waiting_count = presence.waiting_line_count
        if confirmed_count < slot_limit:
            return 'confirmado', confirmed_count + 1
        return 'espera', waiting_count + 1

    def _promote_waiting_if_possible(self, db: Session, *, group: dict | None, match: dict, position: str) -> None:
        candidates = self.repository.list_waiting_candidates(db, match_id=match['id'], position=position)
        if not candidates:
            return
        release_open = self._within_waitlist_release_window(group=group, match=match)
        chosen = None
        for candidate in candidates:
            if candidate['kind'] == 'guest':
                chosen = candidate
                break
            billing_type = (candidate.get('billing_type') or '').strip().lower()
            if (group or {}).get('group_type') == 'hibrido':
                if billing_type == 'mensalista' and self._is_monthly_adimplente(db, group_id=match['group_id'], user_id=candidate['user_id'], starts_at=match.get('starts_at')):
                    chosen = candidate
                    break
                if billing_type == 'avulso' and release_open:
                    chosen = candidate
                    break
                continue
            chosen = candidate
            break
        if not chosen:
            return
        new_order = self.repository.next_confirmed_order(db, match_id=match['id'], position=position)
        if chosen['kind'] == 'member':
            self.repository.promote_waiting_member(db, participant_id=chosen['entry_id'], queue_order=new_order)
        else:
            self.repository.promote_waiting_guest(db, guest_id=chosen['entry_id'], queue_order=new_order)
        self.repository.clear_saved_draw(db, match_id=match['id'])

    def _invalidate_saved_draw(self, db: Session, *, match_id: str) -> None:
        self.repository.clear_saved_draw(db, match_id=match_id)


    def _self_removal_locked(self, *, match: dict) -> bool:
        starts_at = match.get('starts_at')
        if not starts_at:
            return False
        now = datetime.now(starts_at.tzinfo) if getattr(starts_at, 'tzinfo', None) is not None else datetime.utcnow()
        return now >= (starts_at - timedelta(hours=2))

    def remove_member_presence_as_admin(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, player_id: str) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        group = self.repository.fetch_group(db, group_id=group_id)
        self._ensure_roster_open(match, action='remover participante')
        self._lock_match_presence_scope(db, match_id=match_id)
        current = self.repository.lock_member_presence_row(db, match_id=match_id, player_id=player_id)
        if not current:
            raise HTTPException(status_code=404, detail='Participante não encontrado nesta partida.')
        removed_position = current['position']
        removed_confirmed = current['status'] == 'confirmado'
        self.repository.delete_member_presence(db, participant_id=current['participant_id'])
        self._invalidate_saved_draw(db, match_id=match_id)
        if removed_confirmed:
            self._promote_waiting_if_possible(db, group=group, match=match, position=removed_position)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='presence_removed', title='Presença removida', message='Um participante saiu da lista da partida.', payload={'group_id': group_id, 'match_id': match_id, 'player_id': player_id}, exclude_user_id=principal.user_id)
        self._log_presence_event('presence_admin_remove_applied', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=player_id, removed_confirmed=removed_confirmed, removed_position=removed_position)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def promote_waitlist(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, count: int = 1) -> int:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        group = self.repository.fetch_group(db, group_id=group_id)
        self._ensure_roster_open(match, action='promover lista de espera')
        self._lock_match_presence_scope(db, match_id=match_id)
        promoted = 0
        for _ in range(max(0, int(count or 0))):
            promoted_this_round = False
            for position in ('goleiro', 'linha'):
                before = self._presence_response_from_match(db, match=match)
                confirmed_before = before.confirmed_goalkeeper_count if position == 'goleiro' else before.confirmed_line_count
                slot_limit = before.goalkeeper_slots if position == 'goleiro' else before.line_slots
                if confirmed_before >= slot_limit:
                    continue
                candidates_before = self.repository.list_waiting_candidates(db, match_id=match_id, position=position)
                self._promote_waiting_if_possible(db, group=group, match=match, position=position)
                candidates_after = self.repository.list_waiting_candidates(db, match_id=match_id, position=position)
                if len(candidates_after) < len(candidates_before):
                    promoted += 1
                    promoted_this_round = True
                    break
            if not promoted_this_round:
                break
        if promoted:
            self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match.presence.approved', title='Lista de espera promovida', message='Jogadores foram promovidos da lista de espera para confirmados.', payload={'group_id': group_id, 'match_id': match_id, 'promoted_count': promoted}, exclude_user_id=None)
            self._log_presence_event('presence_waitlist_promoted', group_id=group_id, match_id=match_id, user_id=identity['user_id'], promoted_count=promoted)
            db.commit()
        else:
            db.rollback()
        return promoted

    def approve_member_presence(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, player_id: str, position: str) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_roster_open(match, action='aprovar presença')
        self._lock_match_presence_scope(db, match_id=match_id)

        membership = self.repository.fetch_membership_by_player(db, group_id=group_id, player_id=player_id)
        if not membership or membership.get('status') != 'active':
            raise HTTPException(status_code=404, detail='Participante não encontrado.')

        current = self.repository.lock_member_presence_row(db, match_id=match_id, player_id=player_id)
        presence = self._presence_response_from_match(db, match=match)
        if current:
            if current['status'] == 'confirmado':
                raise HTTPException(status_code=409, detail='Este participante já está confirmado.')
            if current['position'] == 'goleiro':
                presence.waiting_goalkeeper_count = max(0, presence.waiting_goalkeeper_count - 1)
            else:
                presence.waiting_line_count = max(0, presence.waiting_line_count - 1)
        status, queue_order = self._next_status_and_queue(presence, position=position)
        if status != 'confirmado':
            raise HTTPException(status_code=409, detail='Não há vaga disponível para confirmar este jogador nesta posição.')

        if current:
            self.repository.update_member_presence(db, participant_id=current['participant_id'], position=position, status='confirmado', queue_order=queue_order)
        else:
            self.repository.insert_member_presence(db, match_id=match_id, player_id=membership['player_id'], user_id=membership['user_id'], position=position, status='confirmado', queue_order=queue_order)
        self._invalidate_saved_draw(db, match_id=match_id)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match.presence.approved', title='Presença aprovada', message='Um jogador da lista de espera foi aprovado para a lista de confirmados.', payload={'group_id': group_id, 'match_id': match_id, 'player_id': player_id, 'approved_position': position}, exclude_user_id=None)
        self._log_presence_event('presence_member_approved', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=player_id, position=position)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def promote_guest_presence(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, guest_id: str, position: str | None = None) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_roster_open(match, action='promover convidado')
        self._lock_match_presence_scope(db, match_id=match_id)
        guest = self.repository.lock_guest_row(db, match_id=match_id, guest_id=guest_id)
        if not guest:
            raise HTTPException(status_code=404, detail='Convidado não encontrado nesta partida.')
        if guest['status'] == 'confirmado':
            db.rollback()
            return self._presence_response_from_match(db, match=match)
        target_position = (position or guest['position'] or 'linha').strip().lower()
        presence = self._presence_response_from_match(db, match=match)
        if guest['position'] == 'goleiro':
            presence.waiting_goalkeeper_count = max(0, presence.waiting_goalkeeper_count - 1)
        else:
            presence.waiting_line_count = max(0, presence.waiting_line_count - 1)
        status, queue_order = self._next_status_and_queue(presence, position=target_position)
        if status != 'confirmado':
            raise HTTPException(status_code=409, detail='Não há vaga disponível para confirmar este convidado nesta posição.')
        self.repository.update_guest_presence(db, guest_id=guest_id, position=target_position, status='confirmado', queue_order=queue_order)
        self._invalidate_saved_draw(db, match_id=match_id)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match.presence.approved', title='Convidado promovido', message='Um convidado da lista de espera foi promovido para a lista de confirmados.', payload={'group_id': group_id, 'match_id': match_id, 'guest_id': guest_id, 'approved_position': target_position}, exclude_user_id=None)
        self._log_presence_event('presence_guest_promoted', group_id=group_id, match_id=match_id, user_id=identity['user_id'], guest_id=guest_id, position=target_position)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def unapprove_member_presence(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, player_id: str) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_roster_open(match, action='desaprovar presença')
        self._lock_match_presence_scope(db, match_id=match_id)
        current = self.repository.lock_member_presence_row(db, match_id=match_id, player_id=player_id)
        if not current:
            raise HTTPException(status_code=404, detail='Participante não encontrado.')
        if current['status'] != 'confirmado':
            raise HTTPException(status_code=409, detail='Somente participantes confirmados podem ser desaprovados.')
        presence = self._presence_response_from_match(db, match=match)
        approved_by = None
        for item in presence.confirmed:
            if item.player_id == player_id:
                approved_by = (item.approved_by_user_id or '').strip()
                break
        if not approved_by:
            raise HTTPException(status_code=409, detail='Este participante não possui uma aprovação registrável.')
        if approved_by != principal.user_id:
            raise HTTPException(status_code=403, detail='Apenas quem aprovou pode desfazer a aprovação.')
        queue_order = (presence.waiting_goalkeeper_count + 1) if current['position'] == 'goleiro' else (presence.waiting_line_count + 1)
        self.repository.update_member_presence(db, participant_id=current['participant_id'], position=current['position'], status='espera', queue_order=queue_order)
        self._invalidate_saved_draw(db, match_id=match_id)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match.presence.unapproved', title='Aprovação revertida', message='Um jogador voltou da lista de confirmados para a lista de espera.', payload={'group_id': group_id, 'match_id': match_id, 'player_id': player_id}, exclude_user_id=None)
        self._log_presence_event('presence_member_unapproved', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=player_id)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def _build_draw_pool(self, db: Session, *, match_id: str) -> list[dict]:
        rows = self.repository.list_presence(db, match_id=match_id)
        pool: list[dict] = []
        seen_entries: set[str] = set()
        for row in rows:
            if row.get('status') != 'confirmado' or not bool(row.get('has_arrived')):
                continue
            entry_id = row.get('participant_id') or row.get('guest_id') or ''
            if not entry_id or entry_id in seen_entries:
                continue
            seen_entries.add(entry_id)
            pool.append({
                'entry_id': entry_id,
                'kind': row.get('kind') or 'member',
                'participant_id': row.get('participant_id'),
                'guest_id': row.get('guest_id'),
                'player_id': row.get('player_id'),
                'name': row.get('name') or 'Jogador',
                'position': row.get('position') or 'linha',
                'has_arrived': bool(row.get('has_arrived')),
                'skill_rating': row.get('skill_rating'),
            })
        return pool

    def _distribute_teams(self, pool: list[dict], *, players_per_team: int, team_count: int | None = None) -> dict[int, list[dict]]:
        rng = random.SystemRandom()
        eligible = list(pool)
        if not eligible:
            return {}
        if players_per_team <= 0:
            raise HTTPException(status_code=422, detail='Quantidade de jogadores por time inválida.')
        minimum_players = players_per_team * 2
        if len(eligible) < minimum_players:
            raise HTTPException(
                status_code=422,
                detail=f'Jogadores elegíveis insuficientes para sortear 2 times de {players_per_team}. Disponíveis atuais: {len(eligible)}',
            )

        resolved_team_count = int(team_count or 0)
        if resolved_team_count <= 0:
            resolved_team_count = max(2, math.ceil(len(eligible) / players_per_team))
        if resolved_team_count < 2:
            raise HTTPException(status_code=422, detail='É necessário informar pelo menos 2 times para o sorteio.')
        required_players = players_per_team * resolved_team_count
        if len(eligible) < required_players:
            raise HTTPException(
                status_code=422,
                detail=f'Jogadores elegíveis insuficientes para sortear {resolved_team_count} times de {players_per_team}. Disponíveis atuais: {len(eligible)}',
            )
        eligible = eligible[:required_players]

        team_count = resolved_team_count
        teams: dict[int, list[dict]] = {team_number: [] for team_number in range(1, team_count + 1)}
        team_skill_totals = {team_number: 0 for team_number in teams}
        team_goalkeepers = {team_number: 0 for team_number in teams}

        def normalize_position(item: dict) -> str:
            raw = str(item.get('position') or '').strip().lower()
            if raw in {'goalkeeper', 'gk', 'goleiro', 'gol'}:
                return 'goleiro'
            return 'linha'

        def skill_value(item: dict) -> int:
            try:
                return max(1, min(5, int(item.get('skill_rating') or 3)))
            except Exception:
                return 3

        def ordered_items(items: list[dict]) -> list[dict]:
            ordered = list(items)
            rng.shuffle(ordered)
            ordered.sort(key=lambda item: (-skill_value(item), str(item.get('name') or '').lower(), str(item.get('entry_id') or '')))
            return ordered

        def assign_item(item: dict, *, prioritize_goalkeeper: bool = False) -> None:
            available = [team for team, players in teams.items() if len(players) < players_per_team]
            if not available:
                available = list(teams.keys())
            if prioritize_goalkeeper:
                preferred = [team for team in available if team_goalkeepers[team] == 0]
                if preferred:
                    available = preferred
            best_team = min(
                available,
                key=lambda team: (
                    team_goalkeepers[team] if prioritize_goalkeeper else 0,
                    len(teams[team]),
                    team_skill_totals[team],
                    team,
                ),
            )
            teams[best_team].append(item)
            team_skill_totals[best_team] += skill_value(item)
            if normalize_position(item) == 'goleiro':
                team_goalkeepers[best_team] += 1

        goalkeepers = [item for item in eligible if normalize_position(item) == 'goleiro']
        line_players = [item for item in eligible if normalize_position(item) != 'goleiro']

        for item in ordered_items(goalkeepers):
            assign_item(item, prioritize_goalkeeper=True)
        for item in ordered_items(line_players):
            assign_item(item, prioritize_goalkeeper=False)

        return teams

    def _build_game_flow(self, db: Session, *, match: dict) -> MatchGameFlowV2Model:
        draw = self.repository.fetch_saved_draw(db, match_id=match['id'])
        team_numbers: set[int] = set()
        if draw:
            for row in self.repository.list_saved_draw_entries(db, draw_id=draw['draw_id']):
                team_numbers.add(int(row['team_number']))
        ordered_teams = sorted(team_numbers)
        events: list[MatchEventV2Model] = []
        scores: dict[int, int] = {team: 0 for team in ordered_teams}
        for row in self.repository.list_match_events(db, match_id=match['id']):
            entry_id = row.get('participant_id') or row.get('guest_id') or ''
            item = MatchEventV2Model(
                event_id=row['event_id'],
                match_id=row['match_id'],
                team_number=int(row['team_number']),
                entry_id=entry_id,
                kind=row['kind'],
                participant_id=row.get('participant_id'),
                guest_id=row.get('guest_id'),
                player_id=row.get('player_id'),
                display_name=row['display_name'],
                position=row['position'],
                event_type=row['event_type'],
                minute=row.get('minute'),
                notes=row.get('notes'),
                created_at=row['created_at'],
            )
            events.append(item)
            scores.setdefault(item.team_number, 0)
            if item.event_type == 'goal':
                scores[item.team_number] += 1
            elif item.event_type == 'own_goal' and ordered_teams:
                other_teams = [team for team in ordered_teams if team != item.team_number]
                if other_teams:
                    target_team = other_teams[0] if len(other_teams) == 1 else min(other_teams, key=lambda team: scores.get(team, 0))
                    scores[target_team] = scores.get(target_team, 0) + 1
        scoreboard = [MatchScoreTeamV2Model(team_number=team, goals=scores.get(team, 0)) for team in sorted(scores.keys())]
        return MatchGameFlowV2Model(
            match_id=match['id'],
            status=match['status'],
            started_at=match.get('started_at'),
            finished_at=match.get('finished_at'),
            scoreboard=scoreboard,
            events=events,
        )

    def _consolidate_match_stats(self, db: Session, *, match: dict) -> MatchStatsSummaryV2Model:
        draw = self.repository.fetch_saved_draw(db, match_id=match['id'])
        if not draw:
            raise HTTPException(status_code=422, detail='Não existe sorteio salvo para consolidar estatísticas.')
        entries = self.repository.list_saved_draw_entries(db, draw_id=draw['draw_id'])
        events = self.repository.list_match_events(db, match_id=match['id'])
        by_entry: dict[tuple[str, str], dict] = {}
        for row in entries:
            kind = row['kind']
            entry_id = row.get('participant_id') or row.get('guest_id') or ''
            by_entry[(kind, entry_id)] = {
                'team_number': int(row['team_number']),
                'entry_kind': kind,
                'participant_id': row.get('participant_id'),
                'guest_id': row.get('guest_id'),
                'player_id': row.get('player_id'),
                'display_name': row['name'],
                'position': row['position'],
                'goals': 0,
                'assists': 0,
                'own_goals': 0,
                'yellow_cards': 0,
                'red_cards': 0,
            }
        totals = {'goals': 0, 'assists': 0, 'own_goals': 0, 'yellow_cards': 0, 'red_cards': 0}
        for row in events:
            kind = row['kind']
            entry_id = row.get('participant_id') or row.get('guest_id') or ''
            key = (kind, entry_id)
            if key not in by_entry:
                continue
            event_type = row['event_type']
            if event_type == 'goal':
                by_entry[key]['goals'] += 1
                totals['goals'] += 1
            elif event_type == 'assist':
                by_entry[key]['assists'] += 1
                totals['assists'] += 1
            elif event_type == 'own_goal':
                by_entry[key]['own_goals'] += 1
                totals['own_goals'] += 1
            elif event_type == 'yellow_card':
                by_entry[key]['yellow_cards'] += 1
                totals['yellow_cards'] += 1
            elif event_type == 'red_card':
                by_entry[key]['red_cards'] += 1
                totals['red_cards'] += 1
        self.repository.clear_match_player_stats(db, match_id=match['id'])
        for item in by_entry.values():
            self.repository.insert_match_player_stat(db, match_id=match['id'], item=item)
        players = [
            MatchPlayerStatV2Model(
                entry_id=item.get('participant_id') or item.get('guest_id') or '',
                kind=item['entry_kind'],
                team_number=item['team_number'],
                participant_id=item.get('participant_id'),
                guest_id=item.get('guest_id'),
                player_id=item.get('player_id'),
                display_name=item['display_name'],
                position=item['position'],
                goals=item['goals'],
                assists=item['assists'],
                own_goals=item['own_goals'],
                yellow_cards=item['yellow_cards'],
                red_cards=item['red_cards'],
            )
            for item in sorted(by_entry.values(), key=lambda x: (x['team_number'], -x['goals'], -x['assists'], x['display_name']))
        ]
        return MatchStatsSummaryV2Model(match_id=match['id'], status=match['status'], is_consolidated=True, totals=totals, players=players)

    def _get_match_stats(self, db: Session, *, match: dict) -> MatchStatsSummaryV2Model:
        rows = self.repository.list_match_player_stats(db, match_id=match['id'])
        manual_submitted = self.repository.has_manual_match_player_stats(db, match_id=match['id'])
        if rows:
            players = [
                MatchPlayerStatV2Model(
                    entry_id=row.get('participant_id') or row.get('guest_id') or '',
                    kind=row['kind'],
                    team_number=int(row['team_number']),
                    participant_id=row.get('participant_id'),
                    guest_id=row.get('guest_id'),
                    player_id=row.get('player_id'),
                    display_name=row['display_name'],
                    position=row['position'],
                    goals=int(row.get('goals') or 0),
                    assists=int(row.get('assists') or 0),
                    own_goals=int(row.get('own_goals') or 0),
                    yellow_cards=int(row.get('yellow_cards') or 0),
                    red_cards=int(row.get('red_cards') or 0),
                )
                for row in rows
            ]
            totals = {
                'goals': sum(p.goals for p in players),
                'assists': sum(p.assists for p in players),
                'own_goals': sum(p.own_goals for p in players),
                'yellow_cards': sum(p.yellow_cards for p in players),
                'red_cards': sum(p.red_cards for p in players),
            }
            return MatchStatsSummaryV2Model(
                match_id=match['id'],
                status=match['status'],
                is_consolidated=manual_submitted,
                manual_submitted=manual_submitted,
                totals=totals,
                players=players,
            )
        draw = self.repository.fetch_saved_draw(db, match_id=match['id'])
        draw_rows = self.repository.list_saved_draw_entries(db, draw_id=draw['draw_id']) if draw else []
        players = [
            MatchPlayerStatV2Model(
                entry_id=row.get('participant_id') or row.get('guest_id') or '',
                kind=row['kind'],
                team_number=int(row['team_number']),
                participant_id=row.get('participant_id'),
                guest_id=row.get('guest_id'),
                player_id=row.get('player_id'),
                display_name=row['name'],
                position=row['position'],
            )
            for row in draw_rows
        ]
        totals = {'goals': 0, 'assists': 0, 'own_goals': 0, 'yellow_cards': 0, 'red_cards': 0}
        for event in self.repository.list_match_events(db, match_id=match['id']):
            kind = event['kind']
            entry_id = event.get('participant_id') or event.get('guest_id') or ''
            for player in players:
                if player.kind == kind and player.entry_id == entry_id:
                    if event['event_type'] == 'goal':
                        player.goals += 1; totals['goals'] += 1
                    elif event['event_type'] == 'assist':
                        player.assists += 1; totals['assists'] += 1
                    elif event['event_type'] == 'own_goal':
                        player.own_goals += 1; totals['own_goals'] += 1
                    elif event['event_type'] == 'yellow_card':
                        player.yellow_cards += 1; totals['yellow_cards'] += 1
                    elif event['event_type'] == 'red_card':
                        player.red_cards += 1; totals['red_cards'] += 1
                    break
        return MatchStatsSummaryV2Model(match_id=match['id'], status=match['status'], is_consolidated=False, manual_submitted=False, totals=totals, players=players)

    def update_match_settings(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, payload: MatchUpdateV2Request) -> MatchSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)

        current_starts_at = match.get('starts_at')
        current_ends_at = match.get('ends_at')
        current_line_slots = int(match.get('line_slots') or 0)
        current_goalkeeper_slots = int(match.get('goalkeeper_slots') or 0)

        next_starts_at = payload.starts_at or current_starts_at
        next_ends_at = payload.ends_at or current_ends_at
        next_line_slots = payload.line_slots if payload.line_slots is not None else current_line_slots
        next_goalkeeper_slots = payload.goalkeeper_slots if payload.goalkeeper_slots is not None else current_goalkeeper_slots

        class _PayloadView:
            starts_at = next_starts_at
            ends_at = next_ends_at
            line_slots = next_line_slots
            goalkeeper_slots = next_goalkeeper_slots

        self._validate_match_payload(_PayloadView)

        if match.get('status') in {'finished', 'cancelled'}:
            raise HTTPException(status_code=409, detail='Partida finalizada/cancelada não pode ser editada.')

        if payload.draw_locked is True and match.get('draw_status') != 'generated':
            raise HTTPException(status_code=422, detail='Só é possível fixar o sorteio depois de gerar os times.')
        if payload.draw_locked is True and payload.roster_locked is False:
            raise HTTPException(status_code=422, detail='Para fixar o sorteio, mantenha o elenco fechado.')

        payload_data = payload.model_dump(exclude_unset=True)
        group = self.repository.fetch_group(db, group_id=group_id)
        if 'currency' not in payload_data or not payload_data.get('currency'):
            payload_data['currency'] = match.get('currency') or (group.get('currency') if group else None) or 'BRL'
        fallback_fields = (
            'city',
            'payment_method',
            'payment_key',
            'single_waitlist_release_days',
            'modality',
            'gender_type',
        )
        for field in fallback_fields:
            if field not in payload_data or payload_data.get(field) in (None, ''):
                payload_data[field] = match.get(field) or (group.get(field) if group else None)
        self.repository.update_match(db, match_id=match_id, payload=payload_data)

        roster_locked = payload.roster_locked
        draw_locked = payload.draw_locked
        if draw_locked is True and roster_locked is None:
            roster_locked = True
        if roster_locked is not None or draw_locked is not None:
            self.repository.set_match_locks(db, match_id=match_id, roster_locked=roster_locked, draw_locked=draw_locked)

        db.commit()
        self._invalidate_group_cache(group_id=group_id, match_id=match_id)
        return MatchSummaryV2Model(**self._match_or_404(db, group_id=group_id, match_id=match_id))

    def create_match(self, db: Session, principal: SupabasePrincipal, group_id: str, payload: MatchCreateV2Request) -> MatchSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        group = self.repository.fetch_group(db, group_id=group_id)
        if not group or not group.get('is_active'):
            raise HTTPException(status_code=404, detail='Grupo não encontrado.')
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        self._validate_match_payload(payload)
        payload_data = payload.model_dump()
        if not payload_data.get('currency'):
            payload_data['currency'] = group.get('currency') or 'BRL'
        if not payload_data.get('payment_method'):
            payload_data['payment_method'] = group.get('payment_method')
        if not payload_data.get('payment_key'):
            payload_data['payment_key'] = group.get('payment_key')
        if not payload_data.get('city'):
            payload_data['city'] = group.get('city')
        if not payload_data.get('single_waitlist_release_days'):
            payload_data['single_waitlist_release_days'] = int(group.get('single_waitlist_release_days') or 0)
        if not payload_data.get('modality'):
            payload_data['modality'] = group.get('modality')
        if not payload_data.get('gender_type'):
            payload_data['gender_type'] = group.get('gender_type')
        match_id = self.repository.create_match(db, group_id=group_id, created_by_user_id=identity['user_id'], payload=payload_data)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match_created', title='Nova partida criada', message='Uma nova partida foi criada no grupo.', payload={'group_id': group_id, 'match_id': match_id}, exclude_user_id=principal.user_id)
        db.commit()
        self._invalidate_group_cache(group_id=group_id, match_id=match_id)
        return MatchSummaryV2Model(**self._match_or_404(db, group_id=group_id, match_id=match_id))

    def list_group_matches(self, db: Session, principal: SupabasePrincipal, group_id: str) -> list[MatchSummaryV2Model]:
        identity = self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        group = self.repository.fetch_group(db, group_id=group_id)
        rows = [
            self._apply_group_defaults_to_match(row, group)
            for row in self.repository.list_group_matches(
                db, group_id=group_id, current_user_id=identity['user_id'],
            )
        ]
        return [MatchSummaryV2Model(**row) for row in rows]

    def get_match(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        key = self._cache_key('match', group_id, match_id=match_id, user_id=identity['user_id'])
        return app_cache.get_or_set(key, lambda: MatchSummaryV2Model(**self._match_or_404(db, group_id=group_id, match_id=match_id)), 20)

    def get_presence(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        return self._presence_snapshot(db, match_id=match_id, line_slots=int(match['line_slots']), goalkeeper_slots=int(match['goalkeeper_slots']))

    def upsert_presence(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, payload: MatchPresenceUpsertV2Request) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        membership = self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        group = self.repository.fetch_group(db, group_id=group_id)
        self._ensure_roster_open(match, action='alterar presença')
        self._lock_match_presence_scope(db, match_id=match_id)
        current = self.repository.lock_member_presence_row(db, match_id=match_id, player_id=membership['player_id'])
        presence = self._presence_response_from_match(db, match=match)
        if current:
            if current['status'] == 'confirmado':
                if current['position'] == 'goleiro':
                    presence.confirmed = [item for item in presence.confirmed if item.participant_id != current['participant_id']]
                    presence.confirmed_goalkeeper_count = max(0, presence.confirmed_goalkeeper_count - 1)
                else:
                    presence.confirmed = [item for item in presence.confirmed if item.participant_id != current['participant_id']]
                    presence.confirmed_line_count = max(0, presence.confirmed_line_count - 1)
            else:
                if current['position'] == 'goleiro':
                    presence.waiting = [item for item in presence.waiting if item.participant_id != current['participant_id']]
                    presence.waiting_goalkeeper_count = max(0, presence.waiting_goalkeeper_count - 1)
                else:
                    presence.waiting = [item for item in presence.waiting if item.participant_id != current['participant_id']]
                    presence.waiting_line_count = max(0, presence.waiting_line_count - 1)
        role = (membership.get('role') or '').strip().lower()
        if role in {'owner', 'admin'}:
            status, queue_order = self._next_status_and_queue(presence, position=payload.position)
        else:
            status, queue_order = self._resolve_presence_status(db, group=group, membership=membership, match=match, presence=presence, position=payload.position)
        if current and current['position'] == payload.position and current['status'] == status and int(current.get('queue_order') or 0) == int(queue_order or 0):
            self._log_presence_event('presence_upsert_noop', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=membership['player_id'], position=payload.position, status=status)
            db.rollback()
            return self._presence_response_from_match(db, match=match)
        try:
            if current:
                self.repository.update_member_presence(
                    db,
                    participant_id=current['participant_id'],
                    position=payload.position,
                    status=status,
                    queue_order=queue_order,
                )
                operation = 'updated'
            else:
                self.repository.insert_member_presence(
                    db,
                    match_id=match_id,
                    player_id=membership['player_id'],
                    user_id=identity['user_id'],
                    position=payload.position,
                    status=status,
                    queue_order=queue_order,
                )
                operation = 'created'
        except IntegrityError:
            db.rollback()
            self._lock_match_presence_scope(db, match_id=match_id)
            current = self.repository.lock_member_presence_row(db, match_id=match_id, player_id=membership['player_id'])
            if not current:
                raise
            operation = 'recovered_duplicate'
        self._invalidate_saved_draw(db, match_id=match_id)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='presence_updated', title='Presença atualizada', message='A lista de presença da partida foi atualizada.', payload={'group_id': group_id, 'match_id': match_id}, exclude_user_id=principal.user_id)
        self._log_presence_event('presence_upsert_applied', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=membership['player_id'], operation=operation, position=payload.position, status=status)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def remove_presence(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        membership = self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_roster_open(match, action='remover presença')
        group = self.repository.fetch_group(db, group_id=group_id)
        self._lock_match_presence_scope(db, match_id=match_id)
        current = self.repository.lock_member_presence_row(db, match_id=match_id, player_id=membership['player_id'])
        if not current:
            self._log_presence_event('presence_remove_noop', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=membership['player_id'])
            db.rollback()
            return self._presence_response_from_match(db, match=match)
        if current['status'] == 'confirmado' and self._self_removal_locked(match=match):
            raise HTTPException(status_code=409, detail='Não é possível sair da lista de confirmados a menos de 2 horas do início da partida.')
        removed_position = current['position']
        removed_confirmed = current['status'] == 'confirmado'
        self.repository.delete_member_presence(db, participant_id=current['participant_id'])
        self._invalidate_saved_draw(db, match_id=match_id)
        if removed_confirmed:
            self._promote_waiting_if_possible(db, group=group, match=match, position=removed_position)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='presence_removed', title='Presença removida', message='Um participante saiu da lista da partida.', payload={'group_id': group_id, 'match_id': match_id}, exclude_user_id=principal.user_id)
        self._log_presence_event('presence_remove_applied', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=membership['player_id'], removed_confirmed=removed_confirmed, removed_position=removed_position)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def submit_post_match_stats(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, payload: MatchPostStatsV2Request) -> dict[str, int | bool]:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        ends_at = match.get('ends_at')
        if ends_at is not None and getattr(ends_at, 'tzinfo', None) is None:
            ends_at = ends_at.replace(tzinfo=timezone.utc)

        if not (
            match.get('status') == 'finished'
            or (
                ends_at
                and ends_at <= datetime.now(timezone.utc)
            )
        ):
            raise HTTPException(status_code=409, detail='A partida precisa estar finalizada para lançar os dados do ranking.')
        if match.get('status') != 'finished':
            db.execute(text("""
                update public.matches_v2
                set status = 'finished',
                    finished_at = coalesce(finished_at, now()),
                    updated_at = now()
                where id = cast(:mid as uuid)
                  and group_id = cast(:gid as uuid)
            """), {'mid': match_id, 'gid': group_id})
            db.commit()
            self._invalidate_group_cache(group_id=group_id, match_id=match_id)
            app_cache.invalidate_prefix(f'ranking_v2:group:{group_id}')
            match['status'] = 'finished'

        if self.repository.has_manual_match_player_stats(db, match_id=match_id):
            raise HTTPException(status_code=409, detail='Os dados do ranking desta partida já foram lançados.')

        eligible_rows = self.repository.list_confirmed_member_participants_for_stats(db, match_id=match_id)
        if not eligible_rows:
            raise HTTPException(status_code=422, detail='Não há membros confirmados elegíveis para lançar dados nesta partida.')

        seen_payload: dict[str, dict] = {}
        mvp_count = 0
        for item in payload.players:
            player_id = str(item.player_id).strip()
            if not player_id:
                raise HTTPException(status_code=422, detail='O lançamento contém um jogador inválido.')
            if player_id in seen_payload:
                raise HTTPException(status_code=422, detail='O payload contém jogadores duplicados.')
            normalized = {
                'goals': int(item.goals or 0),
                'assists': int(item.assists or 0),
                'wins': int(item.wins or 0),
                'fair_play': int(item.fair_play or 0),
                'mvp': bool(item.mvp),
            }
            if normalized['wins'] > 99 or normalized['goals'] > 99 or normalized['assists'] > 99:
                raise HTTPException(status_code=422, detail='Os dados do ranking excedem o limite permitido por jogador.')
            if normalized['fair_play'] < 0 or normalized['fair_play'] > 5:
                raise HTTPException(status_code=422, detail='O Fair Play deve estar entre 0 e 5.')
            if normalized['mvp']:
                mvp_count += 1
            seen_payload[player_id] = normalized
        if mvp_count > 1:
            raise HTTPException(status_code=422, detail='Defina no máximo um MVP por partida.')

        valid_player_ids = {row['player_id'] for row in eligible_rows if row.get('player_id')}
        invalid_players = sorted(pid for pid in seen_payload.keys() if pid not in valid_player_ids)
        if invalid_players:
            raise HTTPException(status_code=422, detail='O lançamento contém jogadores que não são membros confirmados desta partida.')

        inserted = 0
        try:
            self.repository.clear_match_player_stats(db, match_id=match_id)
            for row in eligible_rows:
                player_payload = seen_payload.get(row['player_id'], {'goals': 0, 'assists': 0, 'wins': 0, 'fair_play': 0, 'mvp': False})
                self.repository.insert_match_player_stat(
                    db,
                    match_id=match_id,
                    item={
                        'team_number': 1,
                        'entry_kind': 'member',
                        'participant_id': row['participant_id'],
                        'guest_id': None,
                        'player_id': row['player_id'],
                        'display_name': row['display_name'],
                        'position': row['position'],
                        'goals': player_payload['goals'],
                        'assists': player_payload['assists'],
                        'wins': player_payload['wins'],
                        'fair_play': player_payload['fair_play'],
                        'own_goals': 0,
                        'yellow_cards': 0,
                        'red_cards': 0,
                        'mvp': player_payload['mvp'],
                    },
                )
                inserted += 1
            self._invalidate_group_cache(group_id=group_id, match_id=match_id)
            app_cache.invalidate_prefix(f'ranking_v2:group:{group_id}')
            db.commit()
        except ValueError as exc:
            db.rollback()
            if str(exc) == 'ranking_insert_integrity_error':
                raise HTTPException(status_code=422, detail='Não foi possível salvar o ranking desta partida. Verifique se a base está atualizada e tente novamente.')
            raise
        except SQLAlchemyError as exc:
            db.rollback()
            log_event(logger, 'match_stats_save_error', match_id=match_id, group_id=group_id, error=str(exc), error_type=type(exc).__name__)
            raise HTTPException(status_code=500, detail=f'Erro ao gravar dados do ranking: {type(exc).__name__}: {str(exc)[:200]}')
        return {'success': True, 'submitted_count': inserted}

    def mark_self_arrival(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, has_arrived: bool) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        membership = self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_roster_open(match, action='alterar chegada')
        self._lock_match_presence_scope(db, match_id=match_id)
        current = self.repository.lock_member_presence_row(db, match_id=match_id, player_id=membership['player_id'])
        if not current or current['status'] != 'confirmado':
            raise HTTPException(status_code=422, detail='Somente participantes confirmados podem marcar chegada.')
        if bool(current.get('has_arrived')) == bool(has_arrived):
            self._log_presence_event('presence_arrival_noop', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=membership['player_id'], has_arrived=has_arrived)
            db.rollback()
            return self._presence_response_from_match(db, match=match)
        self.repository.set_member_arrived(db, participant_id=current['participant_id'], has_arrived=has_arrived)
        self._invalidate_saved_draw(db, match_id=match_id)
        self._log_presence_event('presence_arrival_applied', group_id=group_id, match_id=match_id, user_id=identity['user_id'], player_id=membership['player_id'], has_arrived=has_arrived)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def create_guest(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, payload: MatchGuestCreateV2Request) -> MatchGuestV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_roster_open(match, action='adicionar convidado')
        self._lock_match_presence_scope(db, match_id=match_id)
        normalized_name = payload.name.strip()
        duplicate = self.repository.find_recent_guest_duplicate(
            db,
            match_id=match_id,
            created_by_user_id=identity['user_id'],
            name=normalized_name,
            position=payload.position,
        )
        if duplicate:
            self._log_presence_event('guest_create_noop_duplicate', group_id=group_id, match_id=match_id, user_id=identity['user_id'], guest_id=duplicate['guest_id'], guest_name=normalized_name, position=payload.position)
            db.rollback()
            return MatchGuestV2Model(**duplicate)
        presence = self._presence_response_from_match(db, match=match)
        if payload.status == 'auto':
            status, queue_order = self._next_status_and_queue(presence, position=payload.position)
        elif payload.status == 'confirmado':
            status = 'confirmado'
            queue_order = (presence.confirmed_goalkeeper_count + 1) if payload.position == 'goleiro' else (presence.confirmed_line_count + 1)
        else:
            status = 'espera'
            queue_order = (presence.waiting_goalkeeper_count + 1) if payload.position == 'goleiro' else (presence.waiting_line_count + 1)
        guest_id = self.repository.create_guest(
            db,
            match_id=match_id,
            created_by_user_id=identity['user_id'],
            name=normalized_name,
            position=payload.position,
            status=status,
            queue_order=queue_order,
            skill_rating=payload.skill_rating or 3,
        )
        self._invalidate_saved_draw(db, match_id=match_id)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='guest_added', title='Convidado adicionado', message='Um convidado foi incluído na partida.', payload={'group_id': group_id, 'match_id': match_id, 'guest_id': guest_id}, exclude_user_id=principal.user_id)
        self._log_presence_event('guest_create_applied', group_id=group_id, match_id=match_id, user_id=identity['user_id'], guest_id=guest_id, guest_name=normalized_name, position=payload.position, status=status)
        db.commit()
        guest = self.repository.fetch_guest(db, match_id=match_id, guest_id=guest_id)
        return MatchGuestV2Model(**guest)

    def list_guests(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> list[MatchGuestV2Model]:
        identity = self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        self._match_or_404(db, group_id=group_id, match_id=match_id)
        rows = [row for row in self.repository.list_presence(db, match_id=match_id) if row.get('kind') == 'guest']
        return [
            MatchGuestV2Model(
                guest_id=row['guest_id'],
                match_id=match_id,
                name=row['name'],
                position=row['position'],
                status=row['status'],
                queue_order=int(row.get('queue_order') or 0),
                has_arrived=bool(row.get('has_arrived')),
                is_paid=bool(row.get('is_paid')),
                skill_rating=row.get('skill_rating'),
            )
            for row in rows
        ]

    def delete_guest(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, guest_id: str) -> dict:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        group = self.repository.fetch_group(db, group_id=group_id)
        self._ensure_roster_open(match, action='remover convidado')
        self._lock_match_presence_scope(db, match_id=match_id)
        guest = self.repository.lock_guest_row(db, match_id=match_id, guest_id=guest_id)
        if not guest:
            self._log_presence_event('guest_remove_noop', group_id=group_id, match_id=match_id, user_id=identity['user_id'], guest_id=guest_id)
            db.rollback()
            return self._presence_response_from_match(db, match=match).model_dump()
        removed_confirmed = guest['status'] == 'confirmado'
        removed_position = guest['position']
        self.repository.delete_guest(db, guest_id=guest_id)
        self._invalidate_saved_draw(db, match_id=match_id)
        if removed_confirmed:
            self._promote_waiting_if_possible(db, group=group, match=match, position=removed_position)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='presence_removed', title='Presença removida', message='Um participante saiu da lista da partida.', payload={'group_id': group_id, 'match_id': match_id}, exclude_user_id=principal.user_id)
        self._log_presence_event('guest_remove_applied', group_id=group_id, match_id=match_id, user_id=identity['user_id'], guest_id=guest_id, removed_confirmed=removed_confirmed, removed_position=removed_position)
        db.commit()
        snapshot = self._presence_response_from_match(db, match=match)
        return snapshot.model_dump()

    def mark_guest_arrival(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, guest_id: str, has_arrived: bool) -> MatchPresenceV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_roster_open(match, action='alterar chegada do convidado')
        self._lock_match_presence_scope(db, match_id=match_id)
        guest = self.repository.lock_guest_row(db, match_id=match_id, guest_id=guest_id)
        if not guest or guest['status'] != 'confirmado':
            raise HTTPException(status_code=422, detail='Somente convidados confirmados podem marcar chegada.')
        if bool(guest.get('has_arrived')) == bool(has_arrived):
            self._log_presence_event('guest_arrival_noop', group_id=group_id, match_id=match_id, user_id=identity['user_id'], guest_id=guest_id, has_arrived=has_arrived)
            db.rollback()
            return self._presence_response_from_match(db, match=match)
        self.repository.set_guest_arrived(db, guest_id=guest_id, has_arrived=has_arrived)
        self._invalidate_saved_draw(db, match_id=match_id)
        self._log_presence_event('guest_arrival_applied', group_id=group_id, match_id=match_id, user_id=identity['user_id'], guest_id=guest_id, has_arrived=has_arrived)
        db.commit()
        return self._presence_response_from_match(db, match=match)

    def get_draw_base(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchDrawBaseV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        presence = self._presence_snapshot(db, match_id=match_id, line_slots=int(match['line_slots']), goalkeeper_slots=int(match['goalkeeper_slots']))
        players = [
            MatchDrawBaseItemV2Model(
                entry_id=item.participant_id or item.guest_id or '',
                kind=item.kind,
                name=item.name,
                position=item.position,
                can_play_draw=item.can_play_draw,
            )
            for item in presence.confirmed
            if item.has_arrived
        ]
        return MatchDrawBaseV2Model(
            match_id=match_id,
            total_confirmed=len(presence.confirmed),
            eligible_count=len(players),
            players=players,
        )

    def generate_draw(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, payload: MatchDrawGenerateV2Request) -> MatchDrawResultV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._ensure_draw_not_locked(match, action='gerar novo sorteio')
        self.repository.lock_match(db, match_id=match_id)
        pool = self._build_draw_pool(db, match_id=match_id)
        if len(pool) < 2:
            raise HTTPException(status_code=422, detail='Não há jogadores confirmados com chegada marcada suficientes para o sorteio.')
        players_per_team = int(payload.players_per_team)
        team_count = int(payload.team_count or 0)
        teams = self._distribute_teams(pool, players_per_team=players_per_team, team_count=team_count)
        self.repository.clear_saved_draw(db, match_id=match_id)
        self.repository.ensure_draw_players_per_team_column(db)
        draw_id = self.repository.create_draw(
            db,
            match_id=match_id,
            generated_by_user_id=identity['user_id'],
            team_count=len(teams),
            players_per_team=players_per_team,
        )
        for team_number, items in teams.items():
            for item in items:
                self.repository.insert_draw_entry(db, draw_id=draw_id, team_number=team_number, item=item)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='draw_generated', title='Sorteio gerado', message='Os times da partida foram sorteados.', payload={'group_id': group_id, 'match_id': match_id, 'eligible_count': len(pool), 'team_count': len(teams), 'players_per_team': players_per_team}, exclude_user_id=None)
        db.commit()
        return self.get_saved_draw(db, principal, group_id, match_id)

    def get_saved_draw(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchDrawResultV2Model:
        identity = self._identity_or_404(db, principal)
        membership = self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        self._match_or_404(db, group_id=group_id, match_id=match_id)
        draw = self.repository.fetch_saved_draw(db, match_id=match_id)
        if not draw:
            raise HTTPException(status_code=404, detail='Ainda não existe sorteio salvo para esta partida.')
        rows = self.repository.list_saved_draw_entries(db, draw_id=draw['draw_id'])
        grouped: dict[int, list[MatchDrawTeamItemV2Model]] = defaultdict(list)
        can_view_skill = membership.get('role') in {'owner', 'admin'}
        for row in rows:
            grouped[int(row['team_number'])].append(
                MatchDrawTeamItemV2Model(
                    entry_id=row.get('participant_id') or row.get('guest_id') or row.get('draw_entry_id') or '',
                    kind=row['kind'],
                    participant_id=row.get('participant_id'),
                    guest_id=row.get('guest_id'),
                    player_id=row.get('player_id'),
                    name=row['name'],
                    position=row['position'],
                    has_arrived=True,
                    can_view_skill=can_view_skill,
                    skill_rating=row.get('skill_rating') if can_view_skill else None,
                )
            )
        teams: list[MatchDrawTeamV2Model] = []
        for team_number in sorted(grouped.keys()):
            players = grouped[team_number]
            visible_skills = [item.skill_rating for item in players if item.skill_rating is not None]
            teams.append(
                MatchDrawTeamV2Model(
                    team_number=team_number,
                    players=players,
                    line_count=sum(1 for item in players if item.position != 'goleiro'),
                    goalkeeper_count=sum(1 for item in players if item.position == 'goleiro'),
                    skill_total=sum(visible_skills) if can_view_skill else None,
                    skill_average=(round(sum(visible_skills) / len(visible_skills), 2) if visible_skills and can_view_skill else None),
                    can_view_metrics=can_view_skill,
                )
            )
        return MatchDrawResultV2Model(
            match_id=match_id,
            draw_id=draw['draw_id'],
            team_count=int(draw['team_count']),
            generated_at=draw['generated_at'],
            generated_by_user_id=draw['generated_by_user_id'],
            eligible_count=sum(len(team.players) for team in teams),
            players_per_team=int(draw.get('players_per_team') or 0) or None,
            can_view_skill=can_view_skill,
            skill_visibility='owner_admin' if can_view_skill else 'hidden_for_member',
            teams=teams,
        )

    def start_match(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        if match['status'] == 'in_progress':
            return MatchSummaryV2Model(**match)
        if match['status'] == 'finished':
            raise HTTPException(status_code=409, detail='A partida já foi finalizada.')
        if match.get('draw_status') != 'generated':
            raise HTTPException(status_code=422, detail='Gere os times antes de iniciar a partida.')
        self.repository.set_match_status(
            db,
            match_id=match_id,
            status='in_progress',
            roster_locked=True,
            draw_locked=True,
            set_started=True,
        )
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match_started', title='Partida iniciada', message='A partida foi iniciada.', payload={'group_id': group_id, 'match_id': match_id}, exclude_user_id=None)
        db.commit()
        self._invalidate_group_cache(group_id=group_id, match_id=match_id)
        return MatchSummaryV2Model(**self._match_or_404(db, group_id=group_id, match_id=match_id))

    def finish_match(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        if match['status'] == 'finished':
            return MatchSummaryV2Model(**match)
        if match['status'] != 'in_progress':
            raise HTTPException(status_code=422, detail='A partida precisa estar em andamento para ser finalizada.')
        self.repository.set_match_status(
            db,
            match_id=match_id,
            status='finished',
            roster_locked=True,
            draw_locked=True,
            set_finished=True,
        )
        finished_match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        self._consolidate_match_stats(db, match=finished_match)
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match_finished', title='Partida finalizada', message='A partida foi finalizada.', payload={'group_id': group_id, 'match_id': match_id}, exclude_user_id=None)
        db.commit()
        self._invalidate_group_cache(group_id=group_id, match_id=match_id)
        return MatchSummaryV2Model(**self._match_or_404(db, group_id=group_id, match_id=match_id))

    def get_game_flow(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchGameFlowV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        key = self._cache_key('game_flow', group_id, match_id=match_id, user_id=identity['user_id'])
        return app_cache.get_or_set(key, lambda: self._build_game_flow(db, match=self._match_or_404(db, group_id=group_id, match_id=match_id)), 10)

    def get_match_stats(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str) -> MatchStatsSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
        key = self._cache_key('stats', group_id, match_id=match_id, user_id=identity['user_id'])
        def _load_stats():
            match = self._match_or_404(db, group_id=group_id, match_id=match_id)
            stats = self._get_match_stats(db, match=match)
            return stats
        return app_cache.get_or_set(key, _load_stats, 25)

    def create_match_event(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, payload: MatchEventCreateV2Request) -> MatchGameFlowV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        if match['status'] != 'in_progress':
            raise HTTPException(status_code=422, detail='Os eventos só podem ser lançados com a partida em andamento.')
        draw = self.repository.fetch_saved_draw(db, match_id=match_id)
        if not draw:
            raise HTTPException(status_code=422, detail='Não existe sorteio salvo para esta partida.')
        draw_entry = self.repository.fetch_saved_draw_entry(db, draw_id=draw['draw_id'], kind=payload.kind, entry_id=payload.entry_id)
        if not draw_entry:
            raise HTTPException(status_code=404, detail='Jogador não encontrado no sorteio salvo da partida.')
        self.repository.create_match_event(
            db,
            match_id=match_id,
            created_by_user_id=identity['user_id'],
            draw_entry=draw_entry,
            event_type=payload.event_type,
            minute=payload.minute,
            notes=(payload.notes or '').strip() or None,
        )
        self._notify_group(db, group_id=group_id, actor_user_id=principal.user_id, event_type='match_event', title='Atualização da partida', message='A partida recebeu um novo evento.', payload={'group_id': group_id, 'match_id': match_id, 'event_type': payload.event_type}, exclude_user_id=None)
        db.commit()
        self._invalidate_group_cache(group_id=group_id, match_id=match_id)
        return self._build_game_flow(db, match=self._match_or_404(db, group_id=group_id, match_id=match_id))

    def delete_match_event(self, db: Session, principal: SupabasePrincipal, group_id: str, match_id: str, event_id: str) -> MatchGameFlowV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        match = self._match_or_404(db, group_id=group_id, match_id=match_id)
        if match['status'] != 'in_progress':
            raise HTTPException(status_code=422, detail='Os eventos só podem ser removidos com a partida em andamento.')
        event = self.repository.fetch_match_event(db, match_id=match_id, event_id=event_id)
        if not event:
            raise HTTPException(status_code=404, detail='Evento não encontrado.')
        self.repository.delete_match_event(db, event_id=event_id)
        db.commit()
        self._invalidate_group_cache(group_id=group_id, match_id=match_id)
        return self._build_game_flow(db, match=self._match_or_404(db, group_id=group_id, match_id=match_id))
