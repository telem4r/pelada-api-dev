from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.core.supabase_storage import resolve_avatar_fields
from app.repositories.groups_v2 import GroupsV2Repository
from app.repositories.notifications_v2 import NotificationsV2Repository
from app.schemas.groups import (
    GroupCreateV2Request,
    GroupInvitationCreateV2Request,
    GroupInvitationV2Model,
    GroupJoinRequestV2Model,
    GroupMemberBillingUpdateV2Request,
    GroupMemberRoleUpdateV2Request,
    GroupMemberSummaryV2Model,
    GroupSummaryV2Model,
    GroupUpdateV2Request,
)



from app.services.finance_v2_service import FinanceV2Service
_COUNTRY_DEFAULTS = {
    'brasil': {'currency': 'BRL', 'payment_method': 'PIX'},
    'portugal': {'currency': 'EUR', 'payment_method': 'MBWAY'},
    'espanha': {'currency': 'EUR', 'payment_method': 'Bizum'},
    'frança': {'currency': 'EUR', 'payment_method': 'Lydia'},
    'franca': {'currency': 'EUR', 'payment_method': 'Lydia'},
    'eua': {'currency': 'USD', 'payment_method': 'PayPal'},
    'estados unidos': {'currency': 'USD', 'payment_method': 'PayPal'},
}


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class GroupsV2Service:
    def __init__(self, repository: GroupsV2Repository | None = None) -> None:
        self.repository = repository or GroupsV2Repository()
        self.finance_service = FinanceV2Service()
        self._notifications_repo = NotificationsV2Repository()

    @staticmethod
    def _normalize_group_type(value: str | None) -> str:
        normalized = (value or '').strip().lower()
        if 'hibrid' in normalized or 'hybrid' in normalized:
            return 'hibrido'
        if 'avulso' in normalized:
            return 'avulso'
        return normalized

    def _is_hybrid_group(self, group: dict | None) -> bool:
        return self._normalize_group_type((group or {}).get('group_type')) == 'hibrido'

    def _identity_or_404(self, db: Session, principal: SupabasePrincipal) -> dict:
        identity = self.repository.fetch_foundation_identity(db, user_id=principal.user_id)
        if not identity:
            raise HTTPException(status_code=404, detail='Sessão BoraFut não bootstrapada para este utilizador.')
        return identity

    def _require_active_membership(self, db: Session, group_id: str, user_id: str) -> dict:
        membership = self.repository.fetch_membership(db, group_id=group_id, user_id=user_id)
        if not membership or membership.get('membership_status') != 'active':
            raise HTTPException(status_code=403, detail='Você ainda não é membro ativo deste grupo.')
        return resolve_avatar_fields(membership)

    def _require_admin(self, db: Session, group_id: str, user_id: str) -> dict:
        membership = self._require_active_membership(db, group_id, user_id)
        if membership.get('role') not in {'owner', 'admin'}:
            raise HTTPException(status_code=403, detail='Somente owner ou admin podem executar esta ação.')
        return resolve_avatar_fields(membership)

    def _require_owner(self, db: Session, group_id: str, user_id: str) -> dict:
        membership = self._require_active_membership(db, group_id, user_id)
        if membership.get('role') != 'owner':
            raise HTTPException(status_code=403, detail='Somente o owner pode executar esta ação.')
        return resolve_avatar_fields(membership)

    def create_group(self, db: Session, principal: SupabasePrincipal, payload: GroupCreateV2Request) -> GroupSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        country = _clean_text(payload.country)
        defaults = _COUNTRY_DEFAULTS.get((country or '').lower(), {})
        group_type = payload.group_type
        per_person_cost = payload.per_person_cost if group_type == 'avulso' else None
        monthly_cost = payload.monthly_cost if group_type == 'hibrido' else None
        single_cost = payload.single_cost if group_type == 'hibrido' else None
        waitlist_release_days = payload.single_waitlist_release_days or 0 if group_type == 'hibrido' else 0
        group_id = self.repository.create_group(
            db,
            user_id=identity['user_id'],
            player_id=identity['player_id'],
            name=payload.name.strip(),
            description=_clean_text(payload.description),
            group_type=group_type,
            currency=(_clean_text(payload.currency) or defaults.get('currency') or 'BRL').upper(),
            country=country,
            state=_clean_text(payload.state),
            city=_clean_text(payload.city),
            modality=_clean_text(payload.modality),
            gender_type=_clean_text(payload.gender_type),
            payment_method=_clean_text(payload.payment_method) or defaults.get('payment_method'),
            payment_key=_clean_text(payload.payment_key),
            venue_cost=payload.venue_cost,
            per_person_cost=per_person_cost,
            monthly_cost=monthly_cost,
            single_cost=single_cost,
            single_waitlist_release_days=waitlist_release_days,
            payment_due_day=payload.payment_due_day,
            fine_enabled=bool(payload.fine_enabled),
            fine_amount=payload.fine_amount if payload.fine_enabled else None,
            fine_reason=_clean_text(payload.fine_reason) if payload.fine_enabled else None,
            is_public=bool(payload.is_public),
        )
        db.commit()
        summary = self.repository.fetch_group_summary(db, group_id=group_id, user_id=identity['user_id'])
        return GroupSummaryV2Model(**resolve_avatar_fields(summary))

    def list_my_groups(self, db: Session, principal: SupabasePrincipal) -> list[GroupSummaryV2Model]:
        identity = self._identity_or_404(db, principal)
        rows = self.repository.list_my_groups(db, user_id=identity['user_id'])
        return [
            GroupSummaryV2Model(
                id=row['id'],
                name=row['name'],
                description=row.get('description'),
                currency=(row.get('currency') or 'BRL'),
                avatar_url=resolve_avatar_fields({'avatar_url': row.get('avatar_url')}).get('avatar_url'),
                group_type=row.get('group_type'),
                owner_user_id=row['owner_user_id'],
                owner_name=row.get('owner_name'),
                members_count=int(row.get('members_count') or 0),
                is_owner=(row.get('role') == 'owner'),
                is_admin=(row.get('role') in {'owner', 'admin'}),
                join_request_status='member',
                is_active=bool(row.get('is_active')),
            )
            for row in rows
        ]

    def get_group(self, db: Session, principal: SupabasePrincipal, group_id: str) -> GroupSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        row = self.repository.fetch_group_summary(db, group_id=group_id, user_id=identity['user_id'])
        if not row:
            raise HTTPException(status_code=404, detail='Grupo não encontrado.')
        return GroupSummaryV2Model(**resolve_avatar_fields(row))

    def update_group(self, db: Session, principal: SupabasePrincipal, group_id: str, payload: GroupUpdateV2Request) -> GroupSummaryV2Model:
        identity = self._identity_or_404(db, principal)
        group = self.repository.fetch_group_summary(db, group_id=group_id, user_id=identity['user_id'])
        if not group:
            raise HTTPException(status_code=404, detail='Grupo não encontrado.')
        if not (group.get('is_owner') or group.get('is_admin')):
            raise HTTPException(status_code=403, detail='Somente owner ou admin podem alterar o grupo.')

        changes = payload.model_dump(exclude_unset=True)
        next_country = _clean_text(changes.get('country')) if 'country' in changes else group.get('country')
        defaults = _COUNTRY_DEFAULTS.get((next_country or '').lower(), {})
        if 'currency' in changes:
            changes['currency'] = (_clean_text(changes.get('currency')) or defaults.get('currency') or group.get('currency') or 'BRL').upper()
        elif 'country' in changes and not group.get('currency'):
            changes['currency'] = (defaults.get('currency') or 'BRL').upper()
        if 'payment_method' in changes:
            changes['payment_method'] = _clean_text(changes.get('payment_method')) or defaults.get('payment_method') or group.get('payment_method')
        elif 'country' in changes and not group.get('payment_method'):
            changes['payment_method'] = defaults.get('payment_method')

        for key in ('name', 'description', 'country', 'state', 'city', 'modality', 'gender_type', 'payment_key', 'fine_reason'):
            if key in changes:
                changes[key] = _clean_text(changes[key])

        next_group_type = changes.get('group_type') or group.get('group_type')
        if next_group_type == 'avulso':
            changes['monthly_cost'] = None
            changes['single_cost'] = None
            changes['single_waitlist_release_days'] = 0
            changes['payment_due_day'] = None
        elif next_group_type == 'hibrido':
            changes['per_person_cost'] = None

        if changes.get('fine_enabled') is False:
            changes['fine_amount'] = None
            changes['fine_reason'] = None

        self.repository.update_group(db, group_id=group_id, payload=changes)
        if self._normalize_group_type(next_group_type) == 'avulso':
            self.repository.set_active_members_billing_type(db, group_id=group_id, billing_type='avulso')
        db.commit()
        updated = self.repository.fetch_group_summary(db, group_id=group_id, user_id=identity['user_id'])
        return GroupSummaryV2Model(**resolve_avatar_fields(updated))

    def list_members(self, db: Session, principal: SupabasePrincipal, group_id: str) -> list[GroupMemberSummaryV2Model]:
        identity = self._identity_or_404(db, principal)
        membership = self._require_active_membership(db, group_id, identity['user_id'])
        group = self.repository.fetch_group_summary(db, group_id=group_id, user_id=identity['user_id'])
        if not group:
            raise HTTPException(status_code=404, detail='Grupo não encontrado.')
        is_hybrid_group = self._is_hybrid_group(group)
        rows = self.repository.list_group_members(db, group_id=group_id)
        requester_can_manage_group = membership.get('role') in {'owner', 'admin'}

        financial_status_by_player: dict[str, str] = {}
        if is_hybrid_group:
            try:
                now = datetime.utcnow()
                if requester_can_manage_group:
                    # Garante que a competência atual exista antes de refletir o status financeiro na aba de membros.
                    self.finance_service.get_billing_members(
                        db,
                        principal,
                        group_id,
                        year=now.year,
                        month=now.month,
                    )
                obligations = self.finance_service.repository.list_obligations(db, group_id=group_id)
                for item in obligations:
                    if (item.get('source_type') or '').lower() != 'mensalidade':
                        continue
                    if (item.get('status') or '').lower() not in {'aberta', 'parcial', 'pending', 'overdue'}:
                        continue
                    player_id = item.get('player_id')
                    if player_id:
                        financial_status_by_player[player_id] = 'inadimplente'
            except Exception:
                db.rollback()
                financial_status_by_player = {}

        normalized_rows = []
        for row in rows:
            current = dict(row)
            if not is_hybrid_group:
                current['billing_type'] = None
                current['financial_status'] = None
            else:
                if not requester_can_manage_group and current.get('role') == 'owner':
                    current['billing_type'] = None
                is_monthly = (current.get('billing_type') or '').lower() in {'monthly', 'mensalista'}
                can_view_financial_status = requester_can_manage_group or current.get('user_id') == identity['user_id']
                current['financial_status'] = (
                    financial_status_by_player.get(current.get('player_id'), 'adimplente')
                    if is_monthly and can_view_financial_status
                    else None
                )
            if not requester_can_manage_group:
                current['skill_rating'] = None
            normalized_rows.append(GroupMemberSummaryV2Model(**resolve_avatar_fields(current)))
        return normalized_rows

    def get_my_membership(self, db: Session, principal: SupabasePrincipal, group_id: str) -> dict:
        identity = self._identity_or_404(db, principal)
        membership = self.repository.fetch_membership(db, group_id=group_id, user_id=identity['user_id'])
        if not membership:
            raise HTTPException(status_code=404, detail='Membro não encontrado neste grupo.')
        return resolve_avatar_fields(membership)

    def request_join(self, db: Session, principal: SupabasePrincipal, group_id: str) -> GroupJoinRequestV2Model:
        identity = self._identity_or_404(db, principal)
        group = self.repository.fetch_group_summary(db, group_id=group_id, user_id=identity['user_id'])
        if not group:
            raise HTTPException(status_code=404, detail='Grupo não encontrado.')
        if group.get('join_request_status') == 'member':
            raise HTTPException(status_code=400, detail='Você já participa deste grupo.')
        try:
            payload = self.repository.create_join_request(db, group_id=group_id, user_id=identity['user_id'], player_id=identity['player_id'])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        db.commit()
        return GroupJoinRequestV2Model(**resolve_avatar_fields(payload))

    def list_pending_join_requests(self, db: Session, principal: SupabasePrincipal, group_id: str) -> list[GroupJoinRequestV2Model]:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id, identity['user_id'])
        rows = self.repository.list_pending_join_requests(db, group_id=group_id)
        return [GroupJoinRequestV2Model(**resolve_avatar_fields(row)) for row in rows]

    def approve_join_request(self, db: Session, principal: SupabasePrincipal, group_id: str, request_id: str) -> GroupJoinRequestV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id, identity['user_id'])
        try:
            row = self.repository.approve_join_request(db, group_id=group_id, request_id=request_id, reviewer_user_id=identity['user_id'])
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        db.commit()
        return GroupJoinRequestV2Model(**resolve_avatar_fields(row))

    def reject_join_request(self, db: Session, principal: SupabasePrincipal, group_id: str, request_id: str) -> GroupJoinRequestV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id, identity['user_id'])
        try:
            row = self.repository.reject_join_request(db, group_id=group_id, request_id=request_id, reviewer_user_id=identity['user_id'])
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        db.commit()
        return GroupJoinRequestV2Model(**resolve_avatar_fields(row))

    def update_member_role(self, db: Session, principal: SupabasePrincipal, group_id: str, target_user_id: str, payload: GroupMemberRoleUpdateV2Request) -> dict:
        identity = self._identity_or_404(db, principal)
        acting = self._require_owner(db, group_id, identity['user_id'])
        target = self.repository.fetch_member_by_user_id(db, group_id=group_id, user_id=target_user_id)
        if not target or target.get('membership_status') != 'active':
            raise HTTPException(status_code=404, detail='Membro não encontrado neste grupo.')
        if target_user_id == identity['user_id']:
            raise HTTPException(status_code=400, detail='O owner não pode alterar o próprio papel por esta rota.')
        if target.get('role') == 'owner':
            raise HTTPException(status_code=400, detail='O papel do owner não pode ser alterado.')
        row = self.repository.update_member_role(db, group_id=group_id, target_user_id=target_user_id, role=payload.role)
        db.commit()
        return row

    def update_member_billing(self, db: Session, principal: SupabasePrincipal, group_id: str, target_user_id: str, payload: GroupMemberBillingUpdateV2Request) -> dict:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id, identity['user_id'])
        row = self.repository.update_member_billing(db, group_id=group_id, target_user_id=target_user_id, billing_type=payload.billing_type)
        if not row:
            raise HTTPException(status_code=404, detail='Membro não encontrado neste grupo.')
        db.commit()
        return row

    def update_member_skill_rating(self, db: Session, principal: SupabasePrincipal, group_id: str, target_user_id: str, payload: dict) -> dict:
        from sqlalchemy import text as sa_text
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id, identity['user_id'])
        skill_rating = int(payload.get('skill_rating', 3))
        if skill_rating < 1 or skill_rating > 5:
            raise HTTPException(status_code=400, detail='skill_rating deve ser entre 1 e 5.')
        result = db.execute(sa_text("""
            UPDATE public.group_members SET skill_rating = :sr, updated_at = now()
            WHERE group_id = cast(:gid as uuid) AND user_id = cast(:uid as uuid) AND status = 'active'
            RETURNING user_id::text, player_id::text, skill_rating
        """), {'gid': group_id, 'uid': target_user_id, 'sr': skill_rating}).mappings().first()
        if not result:
            raise HTTPException(status_code=404, detail='Membro não encontrado neste grupo.')
        db.commit()
        return dict(result)

    def remove_member(self, db: Session, principal: SupabasePrincipal, group_id: str, target_user_id: str) -> dict:
        identity = self._identity_or_404(db, principal)
        acting = self._require_admin(db, group_id, identity['user_id'])
        target = self.repository.fetch_member_by_user_id(db, group_id=group_id, user_id=target_user_id)
        if not target or target.get('membership_status') != 'active':
            raise HTTPException(status_code=404, detail='Membro não encontrado neste grupo.')
        if target.get('role') == 'owner':
            raise HTTPException(status_code=400, detail='O owner não pode ser removido.')
        if acting.get('role') == 'admin' and target.get('role') == 'admin':
            raise HTTPException(status_code=403, detail='Admin não pode remover outro admin.')
        self.repository.remove_member(db, group_id=group_id, target_user_id=target_user_id)
        db.commit()
        return {'ok': True, 'removed_user_id': target_user_id}

    def leave_group(self, db: Session, principal: SupabasePrincipal, group_id: str) -> dict:
        identity = self._identity_or_404(db, principal)
        membership = self._require_active_membership(db, group_id, identity['user_id'])
        if membership.get('role') == 'owner':
            raise HTTPException(status_code=400, detail='O owner não pode sair do grupo sem transferir a gestão.')
        self.repository.leave_group(db, group_id=group_id, user_id=identity['user_id'])
        db.commit()
        return {'ok': True, 'left_group_id': group_id}

    def create_invitation(self, db: Session, principal: SupabasePrincipal, group_id: str, payload: GroupInvitationCreateV2Request) -> GroupInvitationV2Model:
        identity = self._identity_or_404(db, principal)
        self._require_admin(db, group_id, identity['user_id'])

        invited_email = (payload.email or '').strip().lower()
        if not invited_email:
            raise HTTPException(status_code=400, detail='Email do convite é obrigatório.')
        if invited_email == (identity.get('user_email') or '').strip().lower():
            raise HTTPException(status_code=400, detail='Não é permitido convidar o próprio owner/admin.')

        existing_member = self.repository.fetch_active_member_by_email(db, group_id=group_id, email=invited_email)
        if existing_member:
            raise HTTPException(status_code=400, detail='Este utilizador já é membro ativo do grupo.')

        row = self.repository.create_invitation(
            db,
            group_id=group_id,
            invited_email=invited_email,
            invited_by_user_id=identity['user_id'],
        )

        # ── FIX: Criar notificação V2 para o utilizador convidado ──────────
        # Procura se o e-mail convidado já pertence a um utilizador registado.
        # Se sim, insere uma notificação na tabela notification_events_v2 para
        # que o convite apareça no sininho (central de notificações) do app.
        #
        # Defensivo: falha ao notificar NÃO deve invalidar o convite já criado.
        try:
            invited_identity = self.repository.fetch_user_identity_by_email(
                db, email=invited_email
            )
            invited_user_id = (invited_identity or {}).get('user_id')
            if invited_user_id:
                group_info = self.repository.fetch_group_summary(
                    db,
                    group_id=group_id,
                    user_id=identity['user_id'],
                )
                group_name = (group_info or {}).get('name', 'Grupo')
                inviter_name = (
                    identity.get('display_name')
                    or identity.get('full_name')
                    or identity.get('user_email', 'Admin')
                )
                invite_id = row.get('invitation_id') or row.get('id')
                self._notifications_repo.insert_many(
                    db,
                    recipient_user_ids=[invited_user_id],
                    group_id=group_id,
                    actor_user_id=identity['user_id'],
                    event_type='group_invite',
                    title='Convite para grupo',
                    message=f'Você foi convidado para o grupo {group_name}.',
                    payload={
                        'group_id': group_id,
                        'invite_id': invite_id,
                        'group_name': group_name,
                        'invited_by_name': inviter_name,
                    },
                )
        except Exception as notify_exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "invite_notification_failed: group_id=%s email=%s err=%s",
                group_id, invited_email, notify_exc,
            )
        # ── FIM DO FIX ─────────────────────────────────────────────────────

        db.commit()
        return GroupInvitationV2Model(**row)

    def search_groups(self, db: Session, principal: SupabasePrincipal, query: str) -> list[GroupSummaryV2Model]:
        identity = self._identity_or_404(db, principal)
        normalized_query = (query or '').strip()
        if not normalized_query:
            return []
        rows = self.repository.search_groups(db, user_id=identity['user_id'], query=normalized_query)
        return [GroupSummaryV2Model(**resolve_avatar_fields(row)) for row in rows]
