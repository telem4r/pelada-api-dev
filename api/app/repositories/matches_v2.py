from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


class MatchesV2Repository:
    def _get_match_player_stats_columns(self, db: Session) -> dict[str, dict[str, Any]]:
        rows = db.execute(
            text(
                """
                select
                    column_name,
                    data_type,
                    udt_name
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'match_player_stats_v2'
                """
            )
        ).mappings().all()
        return {str(row['column_name']): dict(row) for row in rows}

    def _match_player_stats_insert_expr(self, column_meta: dict[str, Any], param_name: str) -> str:
        udt_name = str(column_meta.get('udt_name') or '').lower()
        if udt_name == 'uuid':
            return f"cast(:{param_name} as uuid)"
        if udt_name == 'match_position_enum_v2':
            return f"cast(:{param_name} as match_position_enum_v2)"
        return f":{param_name}"

    def lock_match(self, db: Session, *, match_id: str) -> None:
        db.execute(
            text(
                """
                select id
                from public.matches_v2
                where id = cast(:match_id as uuid)
                for update
                """
            ),
            {'match_id': match_id},
        )

    def lock_member_presence_row(self, db: Session, *, match_id: str, player_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    id::text as participant_id,
                    match_id::text as match_id,
                    player_id::text as player_id,
                    user_id::text as user_id,
                    position,
                    status,
                    queue_order,
                    has_arrived,
                    is_paid
                from public.match_participants_v2
                where match_id = cast(:match_id as uuid)
                  and player_id = cast(:player_id as uuid)
                limit 1
                for update
                """
            ),
            {'match_id': match_id, 'player_id': player_id},
        ).mappings().first()
        return dict(row) if row else None

    def lock_guest_row(self, db: Session, *, match_id: str, guest_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select id::text as guest_id, match_id::text as match_id, name, position, status, queue_order, has_arrived, is_paid, skill_rating
                from public.match_guests_v2
                where match_id = cast(:match_id as uuid)
                  and id = cast(:guest_id as uuid)
                limit 1
                for update
                """
            ),
            {'match_id': match_id, 'guest_id': guest_id},
        ).mappings().first()
        return dict(row) if row else None

    def find_recent_guest_duplicate(self, db: Session, *, match_id: str, created_by_user_id: str, name: str, position: str, lookback_seconds: int = 8) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select id::text as guest_id, match_id::text as match_id, name, position, status, queue_order, has_arrived, is_paid, skill_rating
                from public.match_guests_v2
                where match_id = cast(:match_id as uuid)
                  and created_by_user_id = cast(:created_by_user_id as uuid)
                  and lower(trim(name)) = lower(trim(:name))
                  and position = :position
                  and created_at >= now() - make_interval(secs => :lookback_seconds)
                order by created_at desc
                limit 1
                """
            ),
            {
                'match_id': match_id,
                'created_by_user_id': created_by_user_id,
                'name': name,
                'position': position,
                'lookback_seconds': lookback_seconds,
            },
        ).mappings().first()
        return dict(row) if row else None

    def fetch_foundation_identity(self, db: Session, *, user_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    u.id::text as user_id,
                    p.id::text as player_id,
                    p.display_name,
                    p.avatar_url,
                    coalesce(p.rating, null) as rating
                from public.users u
                join public.players p on p.user_id = u.id
                where u.id = cast(:user_id as uuid)
                limit 1
                """
            ),
            {'user_id': user_id},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_membership(self, db: Session, *, group_id: str, user_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    gm.id::text as membership_id,
                    gm.group_id::text as group_id,
                    gm.user_id::text as user_id,
                    gm.player_id::text as player_id,
                    gm.role,
                    gm.status,
                    gm.billing_type
                from public.group_members gm
                where gm.group_id = cast(:group_id as uuid)
                  and gm.user_id = cast(:user_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id, 'user_id': user_id},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_membership_by_player(self, db: Session, *, group_id: str, player_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    gm.id::text as membership_id,
                    gm.group_id::text as group_id,
                    gm.user_id::text as user_id,
                    gm.player_id::text as player_id,
                    gm.role,
                    gm.status,
                    gm.billing_type
                from public.group_members gm
                where gm.group_id = cast(:group_id as uuid)
                  and gm.player_id = cast(:player_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id, 'player_id': player_id},
        ).mappings().first()
        return dict(row) if row else None

    def has_open_monthly_obligation(self, db: Session, *, group_id: str, user_id: str, year: int, month: int) -> bool:
        return bool(
            db.execute(
                text(
                    """
                    select exists(
                        select 1
                        from public.finance_obligations_v2 o
                        where o.group_id = cast(:group_id as uuid)
                          and o.user_id = cast(:user_id as uuid)
                          and o.source_type = 'mensalidade'
                          and lower(coalesce(o.status, '')) not in ('paga', 'paid', 'cancelled', 'forgiven')
                          and (
                                (o.competence_year = :year and o.competence_month = :month)
                                or make_date(o.competence_year, o.competence_month, 1) < make_date(:year, :month, 1)
                              )
                    )
                    """
                ),
                {
                    'group_id': group_id,
                    'user_id': user_id,
                    'year': year,
                    'month': month,
                },
            ).scalar()
        )

    def fetch_group(self, db: Session, *, group_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select id::text as id, name, group_type::text as group_type, owner_user_id::text as owner_user_id, is_active,
                       single_waitlist_release_days, currency, city, payment_method, payment_key, modality, gender_type
                from public.groups
                where id = cast(:group_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id},
        ).mappings().first()
        return dict(row) if row else None

    def create_match(self, db: Session, *, group_id: str, created_by_user_id: str, payload: dict[str, Any]) -> str:
        row = db.execute(
            text(
                """
                insert into public.matches_v2 (
                    id,
                    group_id,
                    created_by_user_id,
                    title,
                    status,
                    starts_at,
                    ends_at,
                    location_name,
                    notes,
                    line_slots,
                    goalkeeper_slots,
                    draw_status,
                    value_per_player,
                    price_cents,
                    currency,
                    city,
                    payment_method,
                    payment_key,
                    single_waitlist_release_days,
                    modality,
                    gender_type,
                    is_public,
                    roster_locked,
                    draw_locked,
                    created_at,
                    updated_at
                ) values (
                    gen_random_uuid(),
                    cast(:group_id as uuid),
                    cast(:created_by_user_id as uuid),
                    :title,
                    'scheduled',
                    :starts_at,
                    :ends_at,
                    :location_name,
                    :notes,
                    :line_slots,
                    :goalkeeper_slots,
                    'pending',
                    :value_per_player,
                    :price_cents,
                    :currency,
                    :city,
                    :payment_method,
                    :payment_key,
                    :single_waitlist_release_days,
                    :modality,
                    :gender_type,
                    :is_public,
                    false,
                    false,
                    now(),
                    now()
                )
                returning id::text as id
                """
            ),
            {
                'group_id': group_id,
                'created_by_user_id': created_by_user_id,
                'title': payload.get('title'),
                'starts_at': payload['starts_at'],
                'ends_at': payload['ends_at'],
                'location_name': payload.get('location_name'),
                'notes': payload.get('notes'),
                'line_slots': payload['line_slots'],
                'goalkeeper_slots': payload['goalkeeper_slots'],
                'price_cents': payload.get('price_cents'),
                'currency': payload.get('currency'),
                'city': payload.get('city'),
                'payment_method': payload.get('payment_method'),
                'payment_key': payload.get('payment_key'),
                'single_waitlist_release_days': payload.get('single_waitlist_release_days'),
                'modality': payload.get('modality'),
                'gender_type': payload.get('gender_type'),
                'is_public': payload.get('is_public'),
                'value_per_player': float((payload.get('price_cents') or 0)) / 100.0 if payload.get('price_cents') is not None else 0.0,
            },
        ).mappings().one()
        return str(row['id'])


    def update_match(self, db: Session, *, match_id: str, payload: dict[str, Any]) -> None:
        allowed_columns = {
            'title': 'title',
            'starts_at': 'starts_at',
            'ends_at': 'ends_at',
            'location_name': 'location_name',
            'notes': 'notes',
            'line_slots': 'line_slots',
            'goalkeeper_slots': 'goalkeeper_slots',
            'price_cents': 'price_cents',
            'currency': 'currency',
            'city': 'city',
            'payment_method': 'payment_method',
            'payment_key': 'payment_key',
            'single_waitlist_release_days': 'single_waitlist_release_days',
            'modality': 'modality',
            'gender_type': 'gender_type',
            'is_public': 'is_public',
        }

        sets: list[str] = []
        params: dict[str, Any] = {'match_id': match_id}

        for field, column in allowed_columns.items():
            if field in payload:
                sets.append(f"{column} = :{field}")
                params[field] = payload.get(field)

        if 'price_cents' in payload:
            price_cents = payload.get('price_cents')
            params['value_per_player'] = float((price_cents or 0)) / 100.0 if price_cents is not None else 0.0
            sets.append('value_per_player = :value_per_player')

        if not sets:
            return

        sets.append('updated_at = now()')

        db.execute(
            text(
                f"""
                update public.matches_v2
                set {', '.join(sets)}
                where id = cast(:match_id as uuid)
                """
            ),
            params,
        )

    def list_group_matches(self, db: Session, *, group_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text(
                """
                with presence as (
                    select
                        mp.match_id,
                        count(*) filter (where mp.status = 'confirmado') as confirmed_count,
                        count(*) filter (where mp.status = 'espera') as waiting_count,
                        count(*) filter (where mp.has_arrived is true and mp.status = 'confirmado') as arrived_count
                    from public.match_participants_v2 mp
                    group by mp.match_id
                ),
                guest_presence as (
                    select
                        mg.match_id,
                        count(*) as guests_count,
                        count(*) filter (where mg.has_arrived is true and mg.status = 'confirmado') as arrived_guest_count
                    from public.match_guests_v2 mg
                    group by mg.match_id
                )
                select
                    m.id::text as id,
                    m.group_id::text as group_id,
                    m.created_by_user_id::text as created_by_user_id,
                    m.title,
                    m.status,
                    m.starts_at,
                    m.ends_at,
                    m.started_at,
                    m.finished_at,
                    m.location_name,
                    m.notes,
                    m.line_slots,
                    m.goalkeeper_slots,
                    coalesce(p.confirmed_count, 0) as confirmed_count,
                    coalesce(p.waiting_count, 0) as waiting_count,
                    coalesce(g.guests_count, 0) as guests_count,
                    coalesce(p.arrived_count, 0) + coalesce(g.arrived_guest_count, 0) as arrived_count,
                    m.draw_status,
                    m.value_per_player,
                    m.price_cents,
                    m.currency,
                    m.city,
                    m.payment_method,
                    m.payment_key,
                    m.single_waitlist_release_days,
                    m.modality,
                    m.gender_type,
                    m.is_public,
                    m.roster_locked,
                    m.draw_locked
                from public.matches_v2 m
                left join presence p on p.match_id = m.id
                left join guest_presence g on g.match_id = m.id
                where m.group_id = cast(:group_id as uuid)
                order by m.starts_at desc, m.created_at desc
                """
            ),
            {'group_id': group_id},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_match(self, db: Session, *, group_id: str, match_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                with presence as (
                    select
                        mp.match_id,
                        count(*) filter (where mp.status = 'confirmado') as confirmed_count,
                        count(*) filter (where mp.status = 'espera') as waiting_count,
                        count(*) filter (where mp.has_arrived is true and mp.status = 'confirmado') as arrived_count
                    from public.match_participants_v2 mp
                    group by mp.match_id
                ),
                guests as (
                    select
                        mg.match_id,
                        count(*) as guests_count,
                        count(*) filter (where mg.has_arrived is true and mg.status = 'confirmado') as arrived_guest_count
                    from public.match_guests_v2 mg
                    group by mg.match_id
                )
                select
                    m.id::text as id,
                    m.group_id::text as group_id,
                    m.created_by_user_id::text as created_by_user_id,
                    m.title,
                    m.status,
                    m.starts_at,
                    m.ends_at,
                    m.started_at,
                    m.finished_at,
                    m.location_name,
                    m.notes,
                    m.line_slots,
                    m.goalkeeper_slots,
                    coalesce(p.confirmed_count, 0) as confirmed_count,
                    coalesce(p.waiting_count, 0) as waiting_count,
                    coalesce(g.guests_count, 0) as guests_count,
                    coalesce(p.arrived_count, 0) + coalesce(g.arrived_guest_count, 0) as arrived_count,
                    m.draw_status,
                    m.value_per_player,
                    m.price_cents,
                    m.currency,
                    m.city,
                    m.payment_method,
                    m.payment_key,
                    m.single_waitlist_release_days,
                    m.modality,
                    m.gender_type,
                    m.is_public,
                    m.roster_locked,
                    m.draw_locked
                from public.matches_v2 m
                left join presence p on p.match_id = m.id
                left join guests g on g.match_id = m.id
                where m.group_id = cast(:group_id as uuid)
                  and m.id = cast(:match_id as uuid)
                limit 1
                """
            ),
            {'group_id': group_id, 'match_id': match_id},
        ).mappings().first()
        return dict(row) if row else None

    def fetch_member_presence_row(self, db: Session, *, match_id: str, player_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    id::text as participant_id,
                    match_id::text as match_id,
                    player_id::text as player_id,
                    user_id::text as user_id,
                    position,
                    status,
                    queue_order,
                    has_arrived,
                    is_paid
                from public.match_participants_v2
                where match_id = cast(:match_id as uuid)
                  and player_id = cast(:player_id as uuid)
                limit 1
                """
            ),
            {'match_id': match_id, 'player_id': player_id},
        ).mappings().first()
        return dict(row) if row else None

    def insert_member_presence(self, db: Session, *, match_id: str, player_id: str, user_id: str, position: str, status: str, queue_order: int) -> None:
        db.execute(
            text(
                """
                insert into public.match_participants_v2 (
                    id, match_id, player_id, user_id, position, status, queue_order,
                    has_arrived, is_paid, created_at, updated_at
                ) values (
                    gen_random_uuid(), cast(:match_id as uuid), cast(:player_id as uuid), cast(:user_id as uuid),
                    :position, :status, :queue_order, false, false, now(), now()
                )
                """
            ),
            {
                'match_id': match_id,
                'player_id': player_id,
                'user_id': user_id,
                'position': position,
                'status': status,
                'queue_order': queue_order,
            },
        )

    def update_member_presence(self, db: Session, *, participant_id: str, position: str, status: str, queue_order: int) -> None:
        db.execute(
            text(
                """
                update public.match_participants_v2
                set position = :position,
                    status = :status,
                    queue_order = :queue_order,
                    has_arrived = case when :status = 'confirmado' then has_arrived else false end,
                    updated_at = now()
                where id = cast(:participant_id as uuid)
                """
            ),
            {
                'participant_id': participant_id,
                'position': position,
                'status': status,
                'queue_order': queue_order,
            },
        )

    def set_member_arrived(self, db: Session, *, participant_id: str, has_arrived: bool) -> None:
        db.execute(
            text(
                """
                update public.match_participants_v2
                set has_arrived = :has_arrived,
                    updated_at = now()
                where id = cast(:participant_id as uuid)
                """
            ),
            {'participant_id': participant_id, 'has_arrived': has_arrived},
        )

    def delete_member_presence(self, db: Session, *, participant_id: str) -> None:
        db.execute(text("delete from public.match_participants_v2 where id = cast(:participant_id as uuid)"), {'participant_id': participant_id})

    def fetch_guest(self, db: Session, *, match_id: str, guest_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select id::text as guest_id, match_id::text as match_id, name, position, status, queue_order, has_arrived, is_paid, skill_rating
                from public.match_guests_v2
                where match_id = cast(:match_id as uuid)
                  and id = cast(:guest_id as uuid)
                limit 1
                """
            ),
            {'match_id': match_id, 'guest_id': guest_id},
        ).mappings().first()
        return dict(row) if row else None

    def create_guest(self, db: Session, *, match_id: str, created_by_user_id: str, name: str, position: str, status: str, queue_order: int, skill_rating: int | None = None) -> str:
        row = db.execute(
            text(
                """
                insert into public.match_guests_v2 (
                    id, match_id, created_by_user_id, name, position, status, queue_order,
                    has_arrived, is_paid, skill_rating, created_at, updated_at
                ) values (
                    gen_random_uuid(), cast(:match_id as uuid), cast(:created_by_user_id as uuid), :name, :position, :status, :queue_order,
                    false, false, :skill_rating, now(), now()
                )
                returning id::text as id
                """
            ),
            {
                'match_id': match_id,
                'created_by_user_id': created_by_user_id,
                'name': name,
                'position': position,
                'status': status,
                'queue_order': queue_order,
                'skill_rating': skill_rating,
            },
        ).mappings().one()
        return str(row['id'])


    def update_match(self, db: Session, *, match_id: str, payload: dict[str, Any]) -> None:
        allowed_columns = {
            'title': 'title',
            'starts_at': 'starts_at',
            'ends_at': 'ends_at',
            'location_name': 'location_name',
            'notes': 'notes',
            'line_slots': 'line_slots',
            'goalkeeper_slots': 'goalkeeper_slots',
            'price_cents': 'price_cents',
            'currency': 'currency',
        }

        sets: list[str] = []
        params: dict[str, Any] = {'match_id': match_id}

        for field, column in allowed_columns.items():
            if field in payload:
                sets.append(f"{column} = :{field}")
                params[field] = payload.get(field)

        if 'price_cents' in payload:
            price_cents = payload.get('price_cents')
            params['value_per_player'] = float((price_cents or 0)) / 100.0 if price_cents is not None else 0.0
            sets.append('value_per_player = :value_per_player')

        if not sets:
            return

        sets.append('updated_at = now()')

        db.execute(
            text(
                f"""
                update public.matches_v2
                set {', '.join(sets)}
                where id = cast(:match_id as uuid)
                """
            ),
            params,
        )

    def set_guest_arrived(self, db: Session, *, guest_id: str, has_arrived: bool) -> None:
        db.execute(
            text(
                """
                update public.match_guests_v2
                set has_arrived = :has_arrived,
                    updated_at = now()
                where id = cast(:guest_id as uuid)
                """
            ),
            {'guest_id': guest_id, 'has_arrived': has_arrived},
        )

    def update_guest_presence(self, db: Session, *, guest_id: str, position: str, status: str, queue_order: int) -> None:
        db.execute(
            text(
                """
                update public.match_guests_v2
                set position = :position,
                    status = :status,
                    queue_order = :queue_order,
                    updated_at = now()
                where id = cast(:guest_id as uuid)
                """
            ),
            {
                'guest_id': guest_id,
                'position': position,
                'status': status,
                'queue_order': queue_order,
            },
        )

    def delete_guest(self, db: Session, *, guest_id: str) -> None:
        db.execute(text("delete from public.match_guests_v2 where id = cast(:guest_id as uuid)"), {'guest_id': guest_id})

    def list_presence(self, db: Session, *, match_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text(
                """
                select
                    mp.id::text as participant_id,
                    mp.player_id::text as player_id,
                    mp.user_id::text as user_id,
                    null::text as guest_id,
                    'member'::text as kind,
                    coalesce(
                        nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                        nullif(trim(p.display_name), ''),
                        nullif(trim(p.full_name), ''),
                        nullif(trim(u.name), ''),
                        'Jogador'
                    ) as name,
                    coalesce(p.avatar_url, u.avatar_url) as avatar_url,
                    coalesce(gm.skill_rating, p.rating) as skill_rating,
                    gm.billing_type::text as billing_type,
                    m.group_id::text as group_id,
                    m.starts_at as match_starts_at,
                    mp.position,
                    mp.status,
                    mp.queue_order,
                    mp.is_paid,
                    mp.has_arrived,
                    appr.approved_by_user_id,
                    appr.approved_by_user_name,
                    mp.created_at
                from public.match_participants_v2 mp
                join public.players p on p.id = mp.player_id
                join public.users u on u.id = mp.user_id
                join public.matches_v2 m on m.id = mp.match_id
                left join public.group_members gm on gm.group_id = m.group_id and gm.user_id = mp.user_id and gm.status = cast('active' as membership_status_enum)
                left join lateral (
                    select
                        ne.actor_user_id::text as approved_by_user_id,
                        coalesce(
                            nullif(trim(concat_ws(' ', nullif(trim(au.first_name), ''), nullif(trim(au.last_name), ''))), ''),
                            nullif(trim(ap.display_name), ''),
                            nullif(trim(au.name), ''),
                            nullif(split_part(au.email, '@', 1), ''),
                            'Jogador'
                        ) as approved_by_user_name
                    from public.notification_events_v2 ne
                    left join public.users au on au.id = ne.actor_user_id
                    left join public.players ap on ap.user_id = au.id
                    where ne.group_id = m.group_id
                      and ne.event_type = 'match.presence.approved'
                      and coalesce(ne.payload->>'match_id', '') = mp.match_id::text
                      and coalesce(ne.payload->>'player_id', '') = mp.player_id::text
                    order by ne.created_at desc
                    limit 1
                ) appr on true
                where mp.match_id = cast(:match_id as uuid)
                union all
                select
                    null::text as participant_id,
                    null::text as player_id,
                    null::text as user_id,
                    mg.id::text as guest_id,
                    'guest'::text as kind,
                    mg.name,
                    null::text as avatar_url,
                    mg.skill_rating as skill_rating,
                    null::text as billing_type,
                    null::text as group_id,
                    null::timestamptz as match_starts_at,
                    mg.position,
                    mg.status,
                    mg.queue_order,
                    mg.is_paid,
                    mg.has_arrived,
                    null::text as approved_by_user_id,
                    null::text as approved_by_user_name,
                    mg.created_at
                from public.match_guests_v2 mg
                where mg.match_id = cast(:match_id as uuid)
                order by status asc, position asc, queue_order asc, name asc
                """
            ),
            {'match_id': match_id},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_first_waiting_candidate(self, db: Session, *, match_id: str, position: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select * from (
                    select
                        'member'::text as kind,
                        mp.id::text as entry_id,
                        mp.queue_order,
                        mp.created_at
                    from public.match_participants_v2 mp
                    where mp.match_id = cast(:match_id as uuid)
                      and mp.position = :position
                      and mp.status = 'espera'
                    union all
                    select
                        'guest'::text as kind,
                        mg.id::text as entry_id,
                        mg.queue_order,
                        mg.created_at
                    from public.match_guests_v2 mg
                    where mg.match_id = cast(:match_id as uuid)
                      and mg.position = :position
                      and mg.status = 'espera'
                ) candidates
                order by queue_order asc, created_at asc
                limit 1
                """
            ),
            {'match_id': match_id, 'position': position},
        ).mappings().first()
        return dict(row) if row else None

    def list_waiting_candidates(self, db: Session, *, match_id: str, position: str) -> list[dict[str, Any]]:
        """Return all waiting candidates for a position, ordered by queue_order/created_at.
        Includes billing_type and user_id so the service layer can apply hybrid-group rules."""
        rows = db.execute(
            text(
                """
                select * from (
                    select
                        'member'::text as kind,
                        mp.id::text as entry_id,
                        mp.user_id::text as user_id,
                        mp.queue_order,
                        mp.created_at,
                        gm.billing_type::text as billing_type
                    from public.match_participants_v2 mp
                    join public.matches_v2 m on m.id = mp.match_id
                    left join public.group_members gm
                        on gm.group_id = m.group_id
                       and gm.user_id = mp.user_id
                       and gm.status = cast('active' as membership_status_enum)
                    where mp.match_id = cast(:match_id as uuid)
                      and mp.position = :position
                      and mp.status = 'espera'
                    union all
                    select
                        'guest'::text as kind,
                        mg.id::text as entry_id,
                        null::text as user_id,
                        mg.queue_order,
                        mg.created_at,
                        null::text as billing_type
                    from public.match_guests_v2 mg
                    where mg.match_id = cast(:match_id as uuid)
                      and mg.position = :position
                      and mg.status = 'espera'
                ) candidates
                order by queue_order asc, created_at asc
                """
            ),
            {'match_id': match_id, 'position': position},
        ).mappings().all()
        return [dict(r) for r in rows]

    def next_confirmed_order(self, db: Session, *, match_id: str, position: str) -> int:
        row = db.execute(
            text(
                """
                select (
                    coalesce((select count(*) from public.match_participants_v2 where match_id = cast(:match_id as uuid) and position = :position and status = 'confirmado'), 0)
                    +
                    coalesce((select count(*) from public.match_guests_v2 where match_id = cast(:match_id as uuid) and position = :position and status = 'confirmado'), 0)
                ) as total
                """
            ),
            {'match_id': match_id, 'position': position},
        ).mappings().one()
        return int(row['total']) + 1

    def promote_waiting_member(self, db: Session, *, participant_id: str, queue_order: int) -> None:
        db.execute(
            text(
                """
                update public.match_participants_v2
                set status = 'confirmado',
                    queue_order = :queue_order,
                    updated_at = now()
                where id = cast(:participant_id as uuid)
                """
            ),
            {'participant_id': participant_id, 'queue_order': queue_order},
        )

    def promote_waiting_guest(self, db: Session, *, guest_id: str, queue_order: int) -> None:
        db.execute(
            text(
                """
                update public.match_guests_v2
                set status = 'confirmado',
                    queue_order = :queue_order,
                    updated_at = now()
                where id = cast(:guest_id as uuid)
                """
            ),
            {'guest_id': guest_id, 'queue_order': queue_order},
        )

    def set_match_locks(self, db: Session, *, match_id: str, roster_locked: bool | None, draw_locked: bool | None) -> None:
        assignments: list[str] = []
        params: dict[str, Any] = {'match_id': match_id}
        if roster_locked is not None:
            assignments.append('roster_locked = :roster_locked')
            params['roster_locked'] = roster_locked
        if draw_locked is not None:
            assignments.append('draw_locked = :draw_locked')
            params['draw_locked'] = draw_locked
        if not assignments:
            return
        assignments.append('updated_at = now()')
        db.execute(text(f"""
                update public.matches_v2
                set {', '.join(assignments)}
                where id = cast(:match_id as uuid)
                """), params)

    def ensure_draw_players_per_team_column(self, db: Session) -> None:
        insp = inspect(db.bind)
        try:
            if not insp.has_table('match_draws_v2'):
                return
            cols = {c['name'] for c in insp.get_columns('match_draws_v2')}
            if 'players_per_team' in cols:
                return
            db.execute(text("ALTER TABLE public.match_draws_v2 ADD COLUMN IF NOT EXISTS players_per_team INTEGER"))
            db.flush()
        except Exception:
            db.rollback()
            db.execute(text("ALTER TABLE public.match_draws_v2 ADD COLUMN IF NOT EXISTS players_per_team INTEGER"))
            db.flush()

    def clear_saved_draw(self, db: Session, *, match_id: str) -> None:
        db.execute(text("delete from public.match_draws_v2 where match_id = cast(:match_id as uuid)"), {'match_id': match_id})
        db.execute(
            text(
                """
                update public.matches_v2
                set draw_status = 'pending', updated_at = now()
                where id = cast(:match_id as uuid)
                """
            ),
            {'match_id': match_id},
        )

    def create_draw(self, db: Session, *, match_id: str, generated_by_user_id: str, team_count: int, players_per_team: int | None = None) -> str:
        row = db.execute(
            text(
                """
                insert into public.match_draws_v2 (
                    id, match_id, generated_by_user_id, team_count, players_per_team, generated_at, created_at, updated_at
                ) values (
                    gen_random_uuid(), cast(:match_id as uuid), cast(:generated_by_user_id as uuid), :team_count, :players_per_team, now(), now(), now()
                )
                on conflict (match_id) do update
                set generated_by_user_id = excluded.generated_by_user_id,
                    team_count = excluded.team_count,
                    players_per_team = excluded.players_per_team,
                    generated_at = now(),
                    updated_at = now()
                returning id::text as id
                """
            ),
            {'match_id': match_id, 'generated_by_user_id': generated_by_user_id, 'team_count': team_count, 'players_per_team': players_per_team},
        ).mappings().one()
        db.execute(
            text(
                """
                update public.matches_v2
                set draw_status = 'generated', updated_at = now()
                where id = cast(:match_id as uuid)
                """
            ),
            {'match_id': match_id},
        )
        return str(row['id'])


    def update_match(self, db: Session, *, match_id: str, payload: dict[str, Any]) -> None:
        allowed_columns = {
            'title': 'title',
            'starts_at': 'starts_at',
            'ends_at': 'ends_at',
            'location_name': 'location_name',
            'notes': 'notes',
            'line_slots': 'line_slots',
            'goalkeeper_slots': 'goalkeeper_slots',
            'price_cents': 'price_cents',
            'currency': 'currency',
        }

        sets: list[str] = []
        params: dict[str, Any] = {'match_id': match_id}

        for field, column in allowed_columns.items():
            if field in payload:
                sets.append(f"{column} = :{field}")
                params[field] = payload.get(field)

        if 'price_cents' in payload:
            price_cents = payload.get('price_cents')
            params['value_per_player'] = float((price_cents or 0)) / 100.0 if price_cents is not None else 0.0
            sets.append('value_per_player = :value_per_player')

        if not sets:
            return

        sets.append('updated_at = now()')

        db.execute(
            text(
                f"""
                update public.matches_v2
                set {', '.join(sets)}
                where id = cast(:match_id as uuid)
                """
            ),
            params,
        )

    def insert_draw_entry(self, db: Session, *, draw_id: str, team_number: int, item: dict[str, Any]) -> None:
        db.execute(
            text(
                """
                insert into public.match_draw_entries_v2 (
                    id,
                    draw_id,
                    team_number,
                    entry_kind,
                    participant_id,
                    guest_id,
                    player_id,
                    display_name,
                    position,
                    skill_rating,
                    created_at
                ) values (
                    gen_random_uuid(),
                    cast(:draw_id as uuid),
                    :team_number,
                    :entry_kind,
                    cast(:participant_id as uuid),
                    cast(:guest_id as uuid),
                    cast(:player_id as uuid),
                    :display_name,
                    :position,
                    :skill_rating,
                    now()
                )
                """
            ),
            {
                'draw_id': draw_id,
                'team_number': team_number,
                'entry_kind': item.get('kind'),
                'participant_id': item.get('participant_id'),
                'guest_id': item.get('guest_id'),
                'player_id': item.get('player_id'),
                'display_name': item.get('name'),
                'position': item.get('position'),
                'skill_rating': item.get('skill_rating'),
            },
        )

    def fetch_saved_draw(self, db: Session, *, match_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    id::text as draw_id,
                    match_id::text as match_id,
                    generated_by_user_id::text as generated_by_user_id,
                    team_count,
                    players_per_team,
                    generated_at
                from public.match_draws_v2
                where match_id = cast(:match_id as uuid)
                limit 1
                """
            ),
            {'match_id': match_id},
        ).mappings().first()
        return dict(row) if row else None

    def list_saved_draw_entries(self, db: Session, *, draw_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text(
                """
                select
                    id::text as draw_entry_id,
                    draw_id::text as draw_id,
                    team_number,
                    entry_kind as kind,
                    participant_id::text as participant_id,
                    guest_id::text as guest_id,
                    player_id::text as player_id,
                    display_name as name,
                    position,
                    skill_rating
                from public.match_draw_entries_v2
                where draw_id = cast(:draw_id as uuid)
                order by team_number asc, position asc, name asc
                """
            ),
            {'draw_id': draw_id},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_saved_draw_entry(self, db: Session, *, draw_id: str, kind: str, entry_id: str) -> dict[str, Any] | None:
        condition = 'participant_id = cast(:entry_id as uuid)' if kind == 'member' else 'guest_id = cast(:entry_id as uuid)'
        row = db.execute(
            text(
                f"""
                select
                    id::text as draw_entry_id,
                    draw_id::text as draw_id,
                    team_number,
                    entry_kind as kind,
                    participant_id::text as participant_id,
                    guest_id::text as guest_id,
                    player_id::text as player_id,
                    display_name as name,
                    position,
                    skill_rating
                from public.match_draw_entries_v2
                where draw_id = cast(:draw_id as uuid)
                  and entry_kind = :kind
                  and {condition}
                limit 1
                """
            ),
            {'draw_id': draw_id, 'kind': kind, 'entry_id': entry_id},
        ).mappings().first()
        return dict(row) if row else None

    def set_match_status(self, db: Session, *, match_id: str, status: str, roster_locked: bool | None = None, draw_locked: bool | None = None, set_started: bool = False, set_finished: bool = False) -> None:
        assignments = ['status = cast(:status as match_status_enum_v2)', 'updated_at = now()']
        params: dict[str, Any] = {'match_id': match_id, 'status': status}
        if roster_locked is not None:
            assignments.append('roster_locked = :roster_locked')
            params['roster_locked'] = roster_locked
        if draw_locked is not None:
            assignments.append('draw_locked = :draw_locked')
            params['draw_locked'] = draw_locked
        if set_started:
            assignments.append('started_at = coalesce(started_at, now())')
        if set_finished:
            assignments.append('finished_at = now()')
        db.execute(
            text(
                f"""
                update public.matches_v2
                set {', '.join(assignments)}
                where id = cast(:match_id as uuid)
                """
            ),
            params,
        )

    def create_match_event(self, db: Session, *, match_id: str, created_by_user_id: str, draw_entry: dict[str, Any], event_type: str, minute: int | None, notes: str | None) -> str:
        row = db.execute(
            text(
                """
                insert into public.match_events_v2 (
                    id,
                    match_id,
                    created_by_user_id,
                    team_number,
                    event_type,
                    participant_id,
                    guest_id,
                    player_id,
                    display_name,
                    position,
                    minute,
                    notes,
                    created_at
                ) values (
                    gen_random_uuid(),
                    cast(:match_id as uuid),
                    cast(:created_by_user_id as uuid),
                    :team_number,
                    :event_type,
                    cast(:participant_id as uuid),
                    cast(:guest_id as uuid),
                    cast(:player_id as uuid),
                    :display_name,
                    :position,
                    :minute,
                    :notes,
                    now()
                )
                returning id::text as id
                """
            ),
            {
                'match_id': match_id,
                'created_by_user_id': created_by_user_id,
                'team_number': draw_entry['team_number'],
                'event_type': event_type,
                'participant_id': draw_entry.get('participant_id'),
                'guest_id': draw_entry.get('guest_id'),
                'player_id': draw_entry.get('player_id'),
                'display_name': draw_entry.get('name'),
                'position': draw_entry.get('position'),
                'minute': minute,
                'notes': notes,
            },
        ).mappings().one()
        return str(row['id'])


    def update_match(self, db: Session, *, match_id: str, payload: dict[str, Any]) -> None:
        allowed_columns = {
            'title': 'title',
            'starts_at': 'starts_at',
            'ends_at': 'ends_at',
            'location_name': 'location_name',
            'notes': 'notes',
            'line_slots': 'line_slots',
            'goalkeeper_slots': 'goalkeeper_slots',
            'price_cents': 'price_cents',
            'currency': 'currency',
        }

        sets: list[str] = []
        params: dict[str, Any] = {'match_id': match_id}

        for field, column in allowed_columns.items():
            if field in payload:
                sets.append(f"{column} = :{field}")
                params[field] = payload.get(field)

        if 'price_cents' in payload:
            price_cents = payload.get('price_cents')
            params['value_per_player'] = float((price_cents or 0)) / 100.0 if price_cents is not None else 0.0
            sets.append('value_per_player = :value_per_player')

        if not sets:
            return

        sets.append('updated_at = now()')

        db.execute(
            text(
                f"""
                update public.matches_v2
                set {', '.join(sets)}
                where id = cast(:match_id as uuid)
                """
            ),
            params,
        )

    def list_match_events(self, db: Session, *, match_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text(
                """
                select
                    id::text as event_id,
                    match_id::text as match_id,
                    team_number,
                    participant_id::text as participant_id,
                    guest_id::text as guest_id,
                    player_id::text as player_id,
                    case when participant_id is not null then 'member' else 'guest' end as kind,
                    display_name,
                    position,
                    event_type,
                    minute,
                    notes,
                    created_at
                from public.match_events_v2
                where match_id = cast(:match_id as uuid)
                order by coalesce(minute, 999) asc, created_at asc
                """
            ),
            {'match_id': match_id},
        ).mappings().all()
        return [dict(r) for r in rows]

    def fetch_match_event(self, db: Session, *, match_id: str, event_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text(
                """
                select
                    id::text as event_id,
                    match_id::text as match_id,
                    team_number,
                    participant_id::text as participant_id,
                    guest_id::text as guest_id,
                    player_id::text as player_id,
                    case when participant_id is not null then 'member' else 'guest' end as kind,
                    display_name,
                    position,
                    event_type,
                    minute,
                    notes,
                    created_at
                from public.match_events_v2
                where match_id = cast(:match_id as uuid)
                  and id = cast(:event_id as uuid)
                limit 1
                """
            ),
            {'match_id': match_id, 'event_id': event_id},
        ).mappings().first()
        return dict(row) if row else None

    def delete_match_event(self, db: Session, *, event_id: str) -> None:
        db.execute(text("delete from public.match_events_v2 where id = cast(:event_id as uuid)"), {'event_id': event_id})


    def clear_match_player_stats(self, db: Session, *, match_id: str) -> None:
        db.execute(text("delete from public.match_player_stats_v2 where match_id = cast(:match_id as uuid)"), {'match_id': match_id})

    def insert_match_player_stat(self, db: Session, *, match_id: str, item: dict[str, Any]) -> str:
        available_columns = self._get_match_player_stats_columns(db)
        payload = {
            'match_id': match_id,
            'team_number': item.get('team_number', 0),
            'entry_kind': item.get('entry_kind'),
            'participant_id': item.get('participant_id'),
            'guest_id': item.get('guest_id'),
            'player_id': item.get('player_id'),
            'display_name': item.get('display_name'),
            'position': item.get('position'),
            'goals': item.get('goals', 0),
            'assists': item.get('assists', 0),
            'wins': item.get('wins', 0),
            'fair_play': item.get('fair_play', 0),
            'own_goals': item.get('own_goals', 0),
            'yellow_cards': item.get('yellow_cards', 0),
            'red_cards': item.get('red_cards', 0),
            'mvp': bool(item.get('mvp', False)),
        }

        insert_columns: list[str] = []
        insert_values: list[str] = []

        if 'id' in available_columns:
            insert_columns.append('id')
            insert_values.append('gen_random_uuid()')

        ordered_payload_columns = [
            'match_id',
            'team_number',
            'entry_kind',
            'participant_id',
            'guest_id',
            'player_id',
            'display_name',
            'position',
            'goals',
            'assists',
            'wins',
            'fair_play',
            'own_goals',
            'yellow_cards',
            'red_cards',
            'mvp',
        ]
        for column_name in ordered_payload_columns:
            column_meta = available_columns.get(column_name)
            if not column_meta:
                continue
            insert_columns.append(column_name)
            insert_values.append(self._match_player_stats_insert_expr(column_meta, column_name))

        if 'created_at' in available_columns:
            insert_columns.append('created_at')
            insert_values.append('now()')
        if 'updated_at' in available_columns:
            insert_columns.append('updated_at')
            insert_values.append('now()')

        statement = text(
            f"""
            insert into public.match_player_stats_v2 (
                {', '.join(insert_columns)}
            ) values (
                {', '.join(insert_values)}
            )
            returning id::text as id
            """
        )

        try:
            row = db.execute(statement, payload).mappings().one()
        except (IntegrityError, SQLAlchemyError) as exc:
            raise ValueError('ranking_insert_integrity_error') from exc
        return str(row['id'])


    def update_match(self, db: Session, *, match_id: str, payload: dict[str, Any]) -> None:
        allowed_columns = {
            'title': 'title',
            'starts_at': 'starts_at',
            'ends_at': 'ends_at',
            'location_name': 'location_name',
            'notes': 'notes',
            'line_slots': 'line_slots',
            'goalkeeper_slots': 'goalkeeper_slots',
            'price_cents': 'price_cents',
            'currency': 'currency',
        }

        sets: list[str] = []
        params: dict[str, Any] = {'match_id': match_id}

        for field, column in allowed_columns.items():
            if field in payload:
                sets.append(f"{column} = :{field}")
                params[field] = payload.get(field)

        if 'price_cents' in payload:
            price_cents = payload.get('price_cents')
            params['value_per_player'] = float((price_cents or 0)) / 100.0 if price_cents is not None else 0.0
            sets.append('value_per_player = :value_per_player')

        if not sets:
            return

        sets.append('updated_at = now()')

        db.execute(
            text(
                f"""
                update public.matches_v2
                set {', '.join(sets)}
                where id = cast(:match_id as uuid)
                """
            ),
            params,
        )

    def has_match_player_stats(self, db: Session, *, match_id: str) -> bool:
        return bool(
            db.execute(
                text(
                    """
                    select exists(
                        select 1
                        from public.match_player_stats_v2
                        where match_id = cast(:match_id as uuid)
                    )
                    """
                ),
                {'match_id': match_id},
            ).scalar()
        )

    def has_manual_match_player_stats(self, db: Session, *, match_id: str) -> bool:
        return bool(
            db.execute(
                text(
                    """
                    select exists(
                        select 1
                        from public.match_player_stats_v2
                        where match_id = cast(:match_id as uuid)
                          and entry_kind = 'member'
                          and team_number = 0
                    )
                    """
                ),
                {'match_id': match_id},
            ).scalar()
        )

    def list_confirmed_member_participants_for_stats(self, db: Session, *, match_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text(
                """
                select
                    mp.id::text as participant_id,
                    mp.player_id::text as player_id,
                    mp.position,
                    coalesce(
                        nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                        nullif(trim(p.display_name), ''),
                        nullif(trim(p.full_name), ''),
                        nullif(trim(u.name), ''),
                        'Jogador'
                    ) as display_name
                from public.match_participants_v2 mp
                join public.players p on p.id = mp.player_id
                left join public.users u on u.id = p.user_id
                where mp.match_id = cast(:match_id as uuid)
                  and mp.status = 'confirmado'
                  and mp.player_id is not null
                order by lower(
                    coalesce(
                        nullif(trim(concat_ws(' ', nullif(trim(u.first_name), ''), nullif(trim(u.last_name), ''))), ''),
                        nullif(trim(p.display_name), ''),
                        nullif(trim(p.full_name), ''),
                        nullif(trim(u.name), ''),
                        'Jogador'
                    )
                ) asc
                """
            ),
            {'match_id': match_id},
        ).mappings().all()
        return [dict(r) for r in rows]

    def list_match_player_stats(self, db: Session, *, match_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text(
                """
                select
                    team_number,
                    entry_kind as kind,
                    participant_id::text as participant_id,
                    guest_id::text as guest_id,
                    player_id::text as player_id,
                    display_name,
                    position,
                    goals,
                    assists,
                    wins,
                    fair_play,
                    own_goals,
                    yellow_cards,
                    red_cards
                from public.match_player_stats_v2
                where match_id = cast(:match_id as uuid)
                order by team_number asc, goals desc, assists desc, display_name asc
                """
            ),
            {'match_id': match_id},
        ).mappings().all()
        return [dict(r) for r in rows]
