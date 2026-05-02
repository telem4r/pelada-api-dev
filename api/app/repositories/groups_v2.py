
from __future__ import annotations

from typing import Any
import secrets

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session


class GroupsV2Repository:


    def _table_columns(self, db: Session, table_name: str) -> set[str]:
        try:
            return {col["name"] for col in inspect(db.bind).get_columns(table_name)}
        except Exception:
            return set()

    def fetch_foundation_identity(self, db: Session, *, user_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    u.id::text as user_id,
                    u.email as user_email,
                    p.id::text as player_id,
                    p.display_name,
                    p.full_name,
                    p.nickname,
                    p.avatar_url
                from public.users u
                join public.players p on p.user_id = u.id
                where u.id = cast(:user_id as uuid)
                limit 1
                """
            ),
            {'user_id': user_id},
        ).mappings().first()
        return dict(row) if row else None

    def create_group(
        self,
        db: Session,
        *,
        user_id: str,
        player_id: str,
        name: str,
        description: str | None,
        group_type: str,
        currency: str,
        country: str | None,
        state: str | None,
        city: str | None,
        modality: str | None,
        gender_type: str | None,
        payment_method: str | None,
        payment_key: str | None,
        venue_cost: float | None,
        per_person_cost: float | None,
        monthly_cost: float | None,
        single_cost: float | None,
        single_waitlist_release_days: int,
        payment_due_day: int | None,
        fine_enabled: bool,
        fine_amount: float | None,
        fine_reason: str | None,
        is_public: bool,
    ) -> str:
        group_id = db.execute(
            text(
                """
                insert into public.groups (
                    id, name, description, group_type, owner_user_id, currency, country, state, city,
                    modality, gender_type, payment_method, payment_key, venue_cost, per_person_cost,
                    monthly_cost, single_cost, single_waitlist_release_days, payment_due_day,
                    fine_enabled, fine_amount, fine_reason, is_public, is_active, created_at, updated_at
                )
                values (
                    gen_random_uuid(), :name, :description, cast(:group_type as group_type_enum), cast(:user_id as uuid),
                    :currency, :country, :state, :city, :modality, :gender_type, :payment_method, :payment_key,
                    :venue_cost, :per_person_cost, :monthly_cost, :single_cost, :single_waitlist_release_days,
                    :payment_due_day, :fine_enabled, :fine_amount, :fine_reason, :is_public, true, now(), now()
                )
                returning id::text
                """
            ),
            {
                'name': name,
                'description': description,
                'group_type': group_type,
                'user_id': user_id,
                'currency': currency,
                'country': country,
                'state': state,
                'city': city,
                'modality': modality,
                'gender_type': gender_type,
                'payment_method': payment_method,
                'payment_key': payment_key,
                'venue_cost': venue_cost,
                'per_person_cost': per_person_cost,
                'monthly_cost': monthly_cost,
                'single_cost': single_cost,
                'single_waitlist_release_days': single_waitlist_release_days,
                'payment_due_day': payment_due_day,
                'fine_enabled': fine_enabled,
                'fine_amount': fine_amount,
                'fine_reason': fine_reason,
                'is_public': is_public,
            },
        ).scalar_one()

        default_billing = 'avulso' if group_type == 'avulso' else 'mensalista'
        db.execute(
            text(
                """
                insert into public.group_members (
                    id, group_id, user_id, player_id, role, status, billing_type, joined_at, created_at, updated_at
                )
                values (
                    gen_random_uuid(), cast(:group_id as uuid), cast(:user_id as uuid), cast(:player_id as uuid), cast('owner' as group_role_enum),
                    cast('active' as membership_status_enum), cast(:billing_type as billing_type_enum), now(), now(), now()
                )
                on conflict (group_id, user_id)
                do update set
                    player_id = excluded.player_id,
                    role = cast('owner' as group_role_enum),
                    status = cast('active' as membership_status_enum),
                    billing_type = cast(:billing_type as billing_type_enum),
                    joined_at = coalesce(public.group_members.joined_at, now()),
                    updated_at = now()
                """
            ),
            {
                'group_id': group_id,
                'user_id': user_id,
                'player_id': player_id,
                'billing_type': default_billing,
            },
        )
        return group_id

    def list_my_groups(self, db: Session, *, user_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text(
                """
                select
                    g.id::text as id,
                    g.name,
                    g.description,
                    coalesce(g.currency, 'BRL') as currency,
                    g.avatar_url,
                    g.group_type::text as group_type,
                    g.is_active,
                    g.owner_user_id::text as owner_user_id,
                    owner_player.display_name as owner_name,
                    gm.role::text as role,
                    gm.status::text as member_status,
                    coalesce(members.members_count, 0) as members_count
                from public.group_members gm
                join public.groups g on g.id = gm.group_id
                join public.players p on p.id = gm.player_id
                left join public.players owner_player on owner_player.user_id = g.owner_user_id
                left join (
                    select group_id, count(*)::int as members_count
                    from public.group_members
                    where status = cast('active' as membership_status_enum)
                    group by group_id
                ) members on members.group_id = g.id
                where gm.user_id = cast(:user_id as uuid)
                  and gm.status = cast('active' as membership_status_enum)
                order by g.created_at desc, g.name asc
                """
            ),
            {'user_id': user_id},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_group_summary(self, db: Session, *, group_id: str, user_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                with my_player as (
                    select id, user_id
                    from public.players
                    where user_id = cast(:user_id as uuid)
                    limit 1
                ),
                membership as (
                    select
                        gm.role::text as role,
                        gm.status::text as status,
                        gm.billing_type::text as billing_type
                    from public.group_members gm
                    where gm.group_id = cast(:group_id as uuid)
                      and gm.user_id = cast(:user_id as uuid)
                    limit 1
                ),
                join_request as (
                    select gjr.status::text as status
                    from public.group_join_requests gjr
                    join my_player mp on mp.id = gjr.player_id
                    where gjr.group_id = cast(:group_id as uuid)
                    order by gjr.created_at desc
                    limit 1
                ),
                members as (
                    select count(*)::int as members_count
                    from public.group_members
                    where group_id = cast(:group_id as uuid)
                      and status = cast('active' as membership_status_enum)
                )
                select
                    g.id::text as id,
                    g.name,
                    g.description,
                    g.currency,
                    g.avatar_url,
                    g.group_type::text as group_type,
                    g.country,
                    g.state,
                    g.city,
                    g.modality,
                    g.gender_type,
                    g.payment_method,
                    g.payment_key,
                    g.venue_cost,
                    g.per_person_cost,
                    g.monthly_cost,
                    g.single_cost,
                    coalesce(g.single_waitlist_release_days, 0) as single_waitlist_release_days,
                    g.payment_due_day,
                    coalesce(g.fine_enabled, false) as fine_enabled,
                    g.fine_amount,
                    g.fine_reason,
                    coalesce(g.is_public, false) as is_public,
                    g.is_active,
                    g.owner_user_id::text as owner_user_id,
                    owner_player.display_name as owner_name,
                    coalesce(members.members_count, 0) as members_count,
                    case when g.owner_user_id = cast(:user_id as uuid) then true else false end as is_owner,
                    case when coalesce(membership.role, '') in ('owner', 'admin') then true else false end as is_admin,
                    case when coalesce(membership.status, '') = 'active' then 'member'
                         when coalesce(join_request.status, '') <> '' then join_request.status
                         else 'none'
                    end as join_request_status,
                    coalesce(membership.role, '') as member_role,
                    coalesce(membership.status, '') as member_status,
                    coalesce(membership.billing_type, '') as billing_type
                from public.groups g
                left join public.players owner_player on owner_player.user_id = g.owner_user_id
                left join membership on true
                left join join_request on true
                left join members on true
                where g.id = cast(:group_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id, 'user_id': user_id},
        ).mappings().first()
        return dict(row) if row else None

    def update_group(self, db: Session, *, group_id: str, payload: dict[str, Any]) -> None:
        fields = []
        params: dict[str, Any] = {'group_id': group_id}
        direct_fields = (
            'name', 'description', 'currency', 'country', 'state', 'city', 'modality',
            'gender_type', 'payment_method', 'payment_key', 'venue_cost', 'per_person_cost',
            'monthly_cost', 'single_cost', 'single_waitlist_release_days', 'payment_due_day',
            'fine_enabled', 'fine_amount', 'fine_reason', 'is_public'
        )
        for key in direct_fields:
            if key in payload:
                fields.append(f"{key} = :{key}")
                params[key] = payload[key]
        if 'group_type' in payload:
            fields.append('group_type = cast(:group_type as group_type_enum)')
            params['group_type'] = payload['group_type']
        if not fields:
            return
        fields.append('updated_at = now()')
        db.execute(text(f"update public.groups set {', '.join(fields)} where id = cast(:group_id as uuid)"), params)

    def list_group_members(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        user_cols = self._table_columns(db, 'users')
        player_cols = self._table_columns(db, 'players')

        user_name_expr = "u.name" if 'name' in user_cols else "null"
        user_first_name_expr = "u.first_name" if 'first_name' in user_cols else "null"
        user_last_name_expr = "u.last_name" if 'last_name' in user_cols else "null"
        user_full_name_expr = (
            f"concat_ws(' ', nullif(trim({user_first_name_expr}), ''), nullif(trim({user_last_name_expr}), ''))"
            if 'first_name' in user_cols or 'last_name' in user_cols
            else "null"
        )
        user_avatar_expr = "u.avatar_url" if 'avatar_url' in user_cols else "null"
        user_birth_city_expr = "u.birth_city" if 'birth_city' in user_cols else ("u.current_city" if 'current_city' in user_cols else "null")
        user_birth_state_expr = "u.birth_state" if 'birth_state' in user_cols else ("u.current_state" if 'current_state' in user_cols else "null")
        user_birth_country_expr = "u.birth_country" if 'birth_country' in user_cols else ("u.current_country" if 'current_country' in user_cols else "null")
        user_birth_date_expr = "u.birth_date" if 'birth_date' in user_cols else "null"
        user_preferred_foot_expr = "u.preferred_foot" if 'preferred_foot' in user_cols else "null"

        player_display_expr = "p.display_name" if 'display_name' in player_cols else "null"
        player_full_expr = "p.full_name" if 'full_name' in player_cols else "null"
        player_avatar_expr = "p.avatar_url" if 'avatar_url' in player_cols else "null"
        player_position_expr = "p.primary_position" if 'primary_position' in player_cols else ("p.position" if 'position' in player_cols else "null")
        player_preferred_foot_expr = "p.preferred_foot" if 'preferred_foot' in player_cols else "null"

        sql = f"""
                select
                    gm.user_id::text as user_id,
                    p.id::text as player_id,
                    gm.role::text as role,
                    gm.status::text as status,
                    gm.billing_type::text as billing_type,
                    gm.skill_rating as skill_rating,
                    json_build_object(
                        'name', coalesce(nullif(trim({user_full_name_expr}), ''), nullif(trim({player_display_expr}), ''), nullif(trim({player_full_expr}), ''), nullif(trim({user_name_expr}), ''), 'Jogador'),
                        'avatar_url', coalesce(nullif(trim({player_avatar_expr}), ''), nullif(trim({user_avatar_expr}), '')),
                        'birth_city', {user_birth_city_expr},
                        'birth_state', {user_birth_state_expr},
                        'birth_country', {user_birth_country_expr}
                    ) as profile,
                    json_build_object(
                        'birth_date', case when {user_birth_date_expr} is not null then to_char({user_birth_date_expr}, 'YYYY-MM-DD') else null end,
                        'position', nullif(trim({player_position_expr}), ''),
                        'preferred_foot', coalesce(nullif(trim({player_preferred_foot_expr}), ''), nullif(trim({user_preferred_foot_expr}), ''))
                    ) as player
                from public.group_members gm
                join public.players p on p.id = gm.player_id
                join public.users u on u.id = gm.user_id
                where gm.group_id = cast(:group_id as uuid)
                  and gm.status = cast('active' as membership_status_enum)
                order by
                    case gm.role::text when 'owner' then 0 when 'admin' then 1 else 2 end,
                    coalesce(nullif(trim({user_full_name_expr}), ''), nullif(trim({player_display_expr}), ''), nullif(trim({player_full_expr}), ''), nullif(trim({user_name_expr}), ''), 'Jogador') asc
                """
        rows = db.execute(text(sql), {'group_id': group_id}).mappings().all()
        return [dict(r) for r in rows]

    def fetch_membership(self, db: Session, *, group_id: str, user_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    gm.group_id::text as group_id,
                    u.id::text as user_id,
                    p.id::text as player_id,
                    gm.role::text as role,
                    case when gm.status::text = 'active' then 'member' else gm.status::text end as status,
                    gm.status::text as membership_status,
                    gm.billing_type::text as billing_type
                from public.group_members gm
                join public.players p on p.id = gm.player_id
                join public.users u on u.id = gm.user_id
                where gm.group_id = cast(:group_id as uuid)
                  and gm.user_id = cast(:user_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id, 'user_id': user_id},
        ).mappings().first()
        return dict(row) if row else None

    def create_join_request(self, db: Session, *, group_id: str, user_id: str, player_id: str) -> dict[str, Any]:
        active_member = self.fetch_membership(db, group_id=group_id, user_id=user_id)
        if active_member and active_member.get('membership_status') == 'active':
            raise ValueError('Você já participa deste grupo.')

        existing = db.execute(
            text(
                """
                select id::text as request_id, status::text as status
                from public.group_join_requests
                where group_id = cast(:group_id as uuid)
                  and player_id = cast(:player_id as uuid)
                order by created_at desc
                limit 1
                """
            ),
            {'group_id': group_id, 'player_id': player_id},
        ).mappings().first()
        if existing and existing['status'] == 'pending':
            return self.fetch_latest_join_request(db, group_id=group_id, player_id=player_id)

        if existing:
            db.execute(
                text(
                    """
                    update public.group_join_requests
                    set status = cast('pending' as membership_status_enum),
                        created_at = now(),
                        reviewed_by_user_id = null,
                        reviewed_at = null
                    where id = cast(:request_id as uuid)
                    """
                ),
                {'request_id': existing['request_id']},
            )
        else:
            db.execute(
                text(
                    """
                    insert into public.group_join_requests (
                        id, group_id, player_id, status, message, created_at
                    ) values (
                        gen_random_uuid(), cast(:group_id as uuid), cast(:player_id as uuid),
                        cast('pending' as membership_status_enum), null, now()
                    )
                    """
                ),
                {'group_id': group_id, 'player_id': player_id},
            )
        return self.fetch_latest_join_request(db, group_id=group_id, player_id=player_id)

    def fetch_latest_join_request(self, db: Session, *, group_id: str, player_id: str) -> dict[str, Any]:
        user_cols = self._table_columns(db, 'users')
        player_cols = self._table_columns(db, 'players')
        user_name_expr = "u.name" if 'name' in user_cols else "null"
        user_first_name_expr = "u.first_name" if 'first_name' in user_cols else "null"
        user_last_name_expr = "u.last_name" if 'last_name' in user_cols else "null"
        user_full_name_expr = (
            f"concat_ws(' ', nullif(trim({user_first_name_expr}), ''), nullif(trim({user_last_name_expr}), ''))"
            if 'first_name' in user_cols or 'last_name' in user_cols
            else "null"
        )
        user_avatar_expr = "u.avatar_url" if 'avatar_url' in user_cols else "null"
        player_display_expr = "p.display_name" if 'display_name' in player_cols else "null"
        player_full_expr = "p.full_name" if 'full_name' in player_cols else "null"
        player_avatar_expr = "p.avatar_url" if 'avatar_url' in player_cols else "null"
        sql = f"""
                select
                    gjr.id::text as request_id,
                    p.user_id::text as user_id,
                    p.id::text as player_id,
                    gjr.status::text as status,
                    'member'::text as role,
                    coalesce(nullif(trim({user_full_name_expr}), ''), nullif(trim({player_display_expr}), ''), nullif(trim({player_full_expr}), ''), nullif(trim({user_name_expr}), ''), 'Jogador') as name,
                    coalesce(nullif(trim({player_avatar_expr}), ''), nullif(trim({user_avatar_expr}), '')) as avatar_url,
                    null::text as billing_type,
                    null::int as skill_rating
                from public.group_join_requests gjr
                join public.players p on p.id = gjr.player_id
                left join public.users u on u.id = p.user_id
                where gjr.group_id = cast(:group_id as uuid)
                  and gjr.player_id = cast(:player_id as uuid)
                order by gjr.created_at desc
                limit 1
                """
        row = db.execute(text(sql), {'group_id': group_id, 'player_id': player_id}).mappings().first()
        return dict(row)

    def list_pending_join_requests(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        user_cols = self._table_columns(db, 'users')
        player_cols = self._table_columns(db, 'players')
        user_name_expr = "u.name" if 'name' in user_cols else "null"
        user_first_name_expr = "u.first_name" if 'first_name' in user_cols else "null"
        user_last_name_expr = "u.last_name" if 'last_name' in user_cols else "null"
        user_full_name_expr = (
            f"concat_ws(' ', nullif(trim({user_first_name_expr}), ''), nullif(trim({user_last_name_expr}), ''))"
            if 'first_name' in user_cols or 'last_name' in user_cols
            else "null"
        )
        user_avatar_expr = "u.avatar_url" if 'avatar_url' in user_cols else "null"
        player_display_expr = "p.display_name" if 'display_name' in player_cols else "null"
        player_full_expr = "p.full_name" if 'full_name' in player_cols else "null"
        player_avatar_expr = "p.avatar_url" if 'avatar_url' in player_cols else "null"
        sql = f"""
                select
                    gjr.id::text as request_id,
                    p.user_id::text as user_id,
                    p.id::text as player_id,
                    gjr.status::text as status,
                    'member'::text as role,
                    coalesce(nullif(trim({user_full_name_expr}), ''), nullif(trim({player_display_expr}), ''), nullif(trim({player_full_expr}), ''), nullif(trim({user_name_expr}), ''), 'Jogador') as name,
                    coalesce(nullif(trim({player_avatar_expr}), ''), nullif(trim({user_avatar_expr}), '')) as avatar_url,
                    null::text as billing_type,
                    null::int as skill_rating
                from public.group_join_requests gjr
                join public.players p on p.id = gjr.player_id
                left join public.users u on u.id = p.user_id
                where gjr.group_id = cast(:group_id as uuid)
                  and gjr.status = cast('pending' as membership_status_enum)
                order by gjr.created_at asc
                """
        rows = db.execute(text(sql), {'group_id': group_id}).mappings().all()
        return [dict(r) for r in rows]

    def fetch_join_request(self, db: Session, *, group_id: str, request_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    gjr.id::text as request_id,
                    gjr.group_id::text as group_id,
                    gjr.player_id::text as player_id,
                    gjr.status::text as status,
                    p.user_id::text as user_id,
                    coalesce(
                        nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                        nullif(trim(p.display_name), ''),
                        nullif(trim(p.full_name), ''),
                        nullif(trim(u.name), ''),
                        'Jogador'
                    ) as name,
                    coalesce(nullif(trim(p.avatar_url), ''), nullif(trim(u.avatar_url), '')) as avatar_url
                from public.group_join_requests gjr
                join public.players p on p.id = gjr.player_id
                left join public.users u on u.id = p.user_id
                where gjr.group_id = cast(:group_id as uuid)
                  and gjr.id = cast(:request_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id, 'request_id': request_id},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_user_identity_by_email(self, db: Session, *, email: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    u.id::text as user_id,
                    lower(u.email) as user_email,
                    p.id::text as player_id
                from public.users u
                join public.players p on p.user_id = u.id
                where lower(u.email) = lower(:email)
                limit 1
                """
            ),
            {'email': email.strip().lower()},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_active_member_by_email(self, db: Session, *, group_id: str, email: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    gm.group_id::text as group_id,
                    gm.user_id::text as user_id,
                    gm.player_id::text as player_id,
                    gm.role::text as role,
                    gm.status::text as membership_status,
                    gm.billing_type::text as billing_type
                from public.group_members gm
                join public.users u on u.id = gm.user_id
                where gm.group_id = cast(:group_id as uuid)
                  and lower(u.email) = lower(:email)
                  and gm.status = cast('active' as membership_status_enum)
                limit 1
                """
            ),
            {'group_id': group_id, 'email': email.strip().lower()},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_group(self, db: Session, *, group_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select id::text as id, name, description, group_type::text as group_type,
                       owner_user_id::text as owner_user_id, is_active
                from public.groups
                where id = cast(:group_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id},
        ).mappings().first()
        return dict(row) if row else None

    def approve_join_request(self, db: Session, *, group_id: str, request_id: str, reviewer_user_id: str) -> dict[str, Any]:
        req = self.fetch_join_request(db, group_id=group_id, request_id=request_id)
        if not req:
            raise ValueError('Solicitação não encontrada.')
        group = self.fetch_group(db, group_id=group_id)
        default_billing = 'avulso'
        db.execute(
            text(
                """
                insert into public.group_members (
                    id, group_id, user_id, player_id, role, status, billing_type, joined_at, created_at, updated_at
                ) values (
                    gen_random_uuid(), cast(:group_id as uuid), cast(:user_id as uuid), cast(:player_id as uuid), cast('member' as group_role_enum),
                    cast('active' as membership_status_enum), cast(:billing_type as billing_type_enum), now(), now(), now()
                )
                on conflict (group_id, user_id)
                do update set
                    player_id = excluded.player_id,
                    role = cast('member' as group_role_enum),
                    status = cast('active' as membership_status_enum),
                    billing_type = cast(:billing_type as billing_type_enum),
                    joined_at = coalesce(public.group_members.joined_at, now()),
                    updated_at = now()
                """
            ),
            {'group_id': group_id, 'user_id': req['user_id'], 'player_id': req['player_id'], 'billing_type': default_billing},
        )
        db.execute(
            text(
                """
                update public.group_join_requests
                set status = cast('active' as membership_status_enum),
                    reviewed_by_user_id = cast(:reviewer_user_id as uuid),
                    reviewed_at = now()
                where id = cast(:request_id as uuid)
                """
            ),
            {'request_id': request_id, 'reviewer_user_id': reviewer_user_id},
        )
        return self.fetch_latest_join_request(db, group_id=group_id, player_id=req['player_id'])

    def reject_join_request(self, db: Session, *, group_id: str, request_id: str, reviewer_user_id: str) -> dict[str, Any]:
        req = self.fetch_join_request(db, group_id=group_id, request_id=request_id)
        if not req:
            raise ValueError('Solicitação não encontrada.')
        db.execute(
            text(
                """
                update public.group_join_requests
                set status = cast('rejected' as membership_status_enum),
                    reviewed_by_user_id = cast(:reviewer_user_id as uuid),
                    reviewed_at = now()
                where id = cast(:request_id as uuid)
                """
            ),
            {'request_id': request_id, 'reviewer_user_id': reviewer_user_id},
        )
        return self.fetch_latest_join_request(db, group_id=group_id, player_id=req['player_id'])

    def fetch_member_by_user_id(self, db: Session, *, group_id: str, user_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    gm.group_id::text as group_id,
                    u.id::text as user_id,
                    p.id::text as player_id,
                    gm.role::text as role,
                    gm.status::text as membership_status,
                    gm.billing_type::text as billing_type
                from public.group_members gm
                join public.players p on p.id = gm.player_id
                join public.users u on u.id = gm.user_id
                where gm.group_id = cast(:group_id as uuid)
                  and gm.user_id = cast(:user_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id, 'user_id': user_id},
        ).mappings().first()
        return dict(row) if row else None

    def update_member_role(self, db: Session, *, group_id: str, target_user_id: str, role: str) -> dict[str, Any] | None:
        db.execute(
            text(
                """
                update public.group_members gm
                set role = cast(:role as group_role_enum),
                    updated_at = now()
                where gm.group_id = cast(:group_id as uuid)
                  and gm.user_id = cast(:target_user_id as uuid)
                  and gm.status = cast('active' as membership_status_enum)
                """
            ),
            {'group_id': group_id, 'target_user_id': target_user_id, 'role': role},
        )
        return self.fetch_member_by_user_id(db, group_id=group_id, user_id=target_user_id)

    def set_active_members_billing_type(self, db: Session, *, group_id: str, billing_type: str) -> None:
        db.execute(
            text(
                '''
                update public.group_members gm
                set billing_type = cast(:billing_type as billing_type_enum),
                    updated_at = now()
                where gm.group_id = cast(:group_id as uuid)
                  and gm.status = cast('active' as membership_status_enum)
                '''
            ),
            {'group_id': group_id, 'billing_type': billing_type},
        )

    def update_member_billing(self, db: Session, *, group_id: str, target_user_id: str, billing_type: str) -> dict[str, Any] | None:
        db.execute(
            text(
                """
                update public.group_members gm
                set billing_type = cast(:billing_type as billing_type_enum),
                    updated_at = now()
                where gm.group_id = cast(:group_id as uuid)
                  and gm.user_id = cast(:target_user_id as uuid)
                  and gm.status = cast('active' as membership_status_enum)
                """
            ),
            {'group_id': group_id, 'target_user_id': target_user_id, 'billing_type': billing_type},
        )
        return self.fetch_member_by_user_id(db, group_id=group_id, user_id=target_user_id)

    def remove_member(self, db: Session, *, group_id: str, target_user_id: str) -> None:
        db.execute(
            text(
                """
                update public.group_members gm
                set status = cast('removed' as membership_status_enum),
                    updated_at = now()
                where gm.group_id = cast(:group_id as uuid)
                  and gm.user_id = cast(:target_user_id as uuid)
                  and gm.status = cast('active' as membership_status_enum)
                """
            ),
            {'group_id': group_id, 'target_user_id': target_user_id},
        )

    def leave_group(self, db: Session, *, group_id: str, user_id: str) -> None:
        self.remove_member(db, group_id=group_id, target_user_id=user_id)

    def create_invitation(self, db: Session, *, group_id: str, invited_email: str, invited_by_user_id: str) -> dict[str, Any]:
        normalized_email = invited_email.strip().lower()

        # check active member (avoid invite existing member)
        existing_member = db.execute(
            text(
                """
                select 1
                from public.group_members gm
                join public.users u on u.id = gm.user_id
                where gm.group_id = cast(:group_id as uuid)
                  and lower(u.email) = lower(:email)
                  and gm.status = cast('active' as membership_status_enum)
                limit 1
                """
            ),
            {"group_id": group_id, "email": normalized_email},
        ).first()

        if existing_member:
            raise ValueError("Usuário já é membro do grupo.")

        # reuse pending invite — return full row
        existing = db.execute(
            text(
                """
                select
                    id::text as invitation_id,
                    group_id::text as group_id,
                    invited_email,
                    status::text as status,
                    token,
                    expires_at::text as expires_at,
                    created_at::text as created_at
                from public.group_invitations
                where group_id = cast(:group_id as uuid)
                  and lower(invited_email) = lower(:email)
                  and status = cast('pending' as membership_status_enum)
                limit 1
                """
            ),
            {"group_id": group_id, "email": normalized_email},
        ).mappings().first()

        if existing:
            return dict(existing)

        token = secrets.token_urlsafe(24)

        row = db.execute(
            text(
                """
                insert into public.group_invitations (
                    id, group_id, invited_email, invited_by_user_id, status, token, expires_at, created_at, updated_at
                ) values (
                    gen_random_uuid(),
                    cast(:group_id as uuid),
                    :email,
                    cast(:invited_by_user_id as uuid),
                    cast('pending' as membership_status_enum),
                    :token,
                    now() + interval '7 days',
                    now(),
                    now()
                )
                returning
                    id::text as invitation_id,
                    group_id::text as group_id,
                    invited_email,
                    status::text as status,
                    token,
                    expires_at::text as expires_at,
                    created_at::text as created_at
                """
            ),
            {
                "group_id": group_id,
                "email": normalized_email,
                "invited_by_user_id": invited_by_user_id,
                "token": token,
            },
        ).mappings().first()

        if not row:
            raise Exception("Falha ao criar convite.")

        return dict(row)

    def search_groups(self, db: Session, *, user_id: str, query: str | None = None) -> list[dict[str, Any]]:
        """Search public/active groups, enriched with caller's membership/join-request status."""
        params: dict[str, Any] = {'user_id': user_id}
        name_filter = ""
        if query and query.strip():
            name_filter = "and lower(g.name) like '%' || lower(:q) || '%'"
            params['q'] = query.strip()

        rows = db.execute(
            text(
                f"""
                with my_player as (
                    select id from public.players where user_id = cast(:user_id as uuid) limit 1
                )
                select
                    g.id::text as id,
                    g.name,
                    g.description,
                    g.currency,
                    g.avatar_url,
                    g.group_type::text as group_type,
                    g.country,
                    g.state,
                    g.city,
                    g.modality,
                    g.gender_type,
                    coalesce(g.is_public, false) as is_public,
                    g.is_active,
                    g.owner_user_id::text as owner_user_id,
                    owner_player.display_name as owner_name,
                    coalesce(members.members_count, 0) as members_count,
                    case when g.owner_user_id = cast(:user_id as uuid) then true else false end as is_owner,
                    case when coalesce(membership.role, '') in ('owner', 'admin') then true else false end as is_admin,
                    case when coalesce(membership.status, '') = 'active' then 'member'
                         when coalesce(jr.status, '') <> '' then jr.status
                         else 'none'
                    end as join_request_status
                from public.groups g
                left join public.players owner_player on owner_player.user_id = g.owner_user_id
                left join (
                    select group_id, count(*)::int as members_count
                    from public.group_members
                    where status = cast('active' as membership_status_enum)
                    group by group_id
                ) members on members.group_id = g.id
                left join (
                    select gm.group_id, gm.role::text as role, gm.status::text as status
                    from public.group_members gm
                    where gm.user_id = cast(:user_id as uuid)
                ) membership on membership.group_id = g.id
                left join lateral (
                    select gjr.status::text as status
                    from public.group_join_requests gjr
                    join my_player mp on mp.id = gjr.player_id
                    where gjr.group_id = g.id
                    order by gjr.created_at desc
                    limit 1
                ) jr on true
                where g.is_active = true
                  {name_filter}
                order by g.name asc
                limit 50
                """
            ),
            params,
        ).mappings().all()
        return [dict(r) for r in rows]

