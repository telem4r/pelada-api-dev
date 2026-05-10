from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.core.supabase_storage import resolve_avatar_fields
from app.schemas.home_v2 import (
    HomeGroupV2Model,
    HomeMatchV2Model,
    HomeNotificationsV2Model,
    HomeProfileV2Model,
    HomeSummaryV2Model,
)


class HomeV2Service:
    """Aggregated Home read model.

    Read-only by design: this service must never create, update or delete
    domain entities. The mobile app can render a local snapshot immediately
    and use this endpoint only to refresh data in the background.
    """

    def _table_columns(self, db: Session, table_name: str) -> set[str]:
        try:
            return {col["name"] for col in inspect(db.bind).get_columns(table_name)}
        except Exception:
            return set()

    @staticmethod
    def _safe_resolve_avatars(payload: Any) -> Any:
        try:
            return resolve_avatar_fields(payload)
        except Exception:
            return payload

    def get_summary(self, db: Session, principal: SupabasePrincipal) -> HomeSummaryV2Model:
        profile = self._fetch_profile(db, user_id=principal.user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Perfil do utilizador não encontrado.")

        groups = self._fetch_groups(db, user_id=principal.user_id)
        upcoming_matches = self._fetch_upcoming_matches(db, user_id=principal.user_id, limit=5)
        next_match = next((item for item in upcoming_matches if item.get("is_current_user_confirmed")), None)
        if next_match is None and upcoming_matches:
            next_match = upcoming_matches[0]
        notifications = self._fetch_notifications(db, user_id=principal.user_id)

        profile = self._safe_resolve_avatars(profile)
        groups = self._safe_resolve_avatars(groups)

        return HomeSummaryV2Model(
            generated_at=datetime.now(timezone.utc),
            profile=HomeProfileV2Model(**profile),
            groups=[HomeGroupV2Model(**item) for item in groups],
            next_match=HomeMatchV2Model(**next_match) if next_match else None,
            upcoming_matches=[HomeMatchV2Model(**item) for item in upcoming_matches],
            notifications=HomeNotificationsV2Model(**notifications),
            flags={
                "home_summary_enabled": True,
                "snapshot_first": True,
                "background_refresh": True,
            },
        )

    def _fetch_profile(self, db: Session, *, user_id: str) -> dict[str, Any] | None:
        user_cols = self._table_columns(db, "users")
        player_cols = self._table_columns(db, "players")

        def u(col: str) -> str:
            return f"u.{col}" if col in user_cols else "null"

        def p(col: str) -> str:
            return f"p.{col}" if col in player_cols else "null"

        user_name_expr = u("name")
        user_first_name_expr = u("first_name")
        user_last_name_expr = u("last_name")
        user_full_name_expr = (
            f"concat_ws(' ', nullif(trim({user_first_name_expr}), ''), nullif(trim({user_last_name_expr}), ''))"
            if "first_name" in user_cols or "last_name" in user_cols
            else "null"
        )
        player_display_expr = p("display_name")
        player_full_expr = p("full_name")
        player_avatar_expr = p("avatar_url")
        user_avatar_expr = u("avatar_url")
        player_position_exprs = [p(col) for col in ("position", "primary_position") if col in player_cols]
        user_position_expr = u("position")
        position_expr = "coalesce(" + ", ".join([f"nullif(trim({expr}), '')" for expr in player_position_exprs + [user_position_expr]]) + ")" if player_position_exprs or "position" in user_cols else "null"
        city_expr = "coalesce(" + ", ".join([
            f"nullif(trim({expr}), '')"
            for expr in [p("city"), u("current_city"), u("birth_city")]
            if expr != "null"
        ]) + ")" if any(col in player_cols for col in ("city",)) or any(col in user_cols for col in ("current_city", "birth_city")) else "null"

        row = db.execute(
            text(f"""
                with reputation as (
                    select
                        target_player_id,
                        count(*)::int as ratings_count,
                        avg((skill + fair_play + commitment) / 3.0)::float as score
                    from public.player_ratings_v2
                    group by target_player_id
                )
                select
                    u.id::text as user_id,
                    p.id::text as player_id,
                    coalesce(
                        nullif(trim({user_full_name_expr}), ''),
                        nullif(trim({user_name_expr}), ''),
                        nullif(trim({player_display_expr}), ''),
                        nullif(trim({player_full_expr}), ''),
                        'Jogador'
                    ) as name,
                    u.email,
                    coalesce(nullif(trim({player_avatar_expr}), ''), nullif(trim({user_avatar_expr}), '')) as avatar_url,
                    {position_expr} as position,
                    {city_expr} as city,
                    case when coalesce(r.ratings_count, 0) = 0 then null else round(r.score::numeric, 1)::float end as reputation_score,
                    case
                        when coalesce(r.ratings_count, 0) = 0 then 'Sem reputação'
                        when r.score >= 4.5 then 'Excelente'
                        when r.score >= 3.5 then 'Bom'
                        when r.score >= 2.5 then 'Regular'
                        else 'Baixo'
                    end as reputation_label
                from public.users u
                left join public.players p on p.user_id = u.id
                left join reputation r on r.target_player_id = p.id
                where u.id = cast(:user_id as uuid)
                limit 1
            """),
            {"user_id": user_id},
        ).mappings().first()
        return dict(row) if row else None

    def _fetch_groups(self, db: Session, *, user_id: str) -> list[dict[str, Any]]:
        rows = db.execute(
            text("""
                select
                    g.id::text as id,
                    g.name,
                    g.avatar_url,
                    g.group_type::text as group_type,
                    gm.role::text as role,
                    gm.status::text as member_status,
                    coalesce(members.members_count, 0)::int as members_count
                from public.group_members gm
                join public.groups g on g.id = gm.group_id
                left join (
                    select group_id, count(*)::int as members_count
                    from public.group_members
                    where status = cast('active' as membership_status_enum)
                    group by group_id
                ) members on members.group_id = g.id
                where gm.user_id = cast(:user_id as uuid)
                  and gm.status = cast('active' as membership_status_enum)
                  and coalesce(g.is_active, true) is true
                order by g.created_at desc, g.name asc
                limit 20
            """),
            {"user_id": user_id},
        ).mappings().all()
        return [dict(row) for row in rows]

    def _fetch_upcoming_matches(self, db: Session, *, user_id: str, limit: int) -> list[dict[str, Any]]:
        rows = db.execute(
            text("""
                with my_groups as (
                    select gm.group_id
                    from public.group_members gm
                    where gm.user_id = cast(:user_id as uuid)
                      and gm.status = cast('active' as membership_status_enum)
                ),
                candidate_matches as (
                    select m.*
                    from public.matches_v2 m
                    where m.group_id in (select group_id from my_groups)
                      and m.starts_at >= now() - interval '2 hours'
                      and lower(coalesce(m.status::text, 'scheduled')) not in ('cancelled', 'canceled', 'finished')
                ),
                presence as (
                    select
                        mp.match_id,
                        count(*) filter (where mp.status = 'confirmado')::int as confirmed_count,
                        count(*) filter (where mp.status = 'espera')::int as waiting_count,
                        count(*) filter (where mp.has_arrived is true and mp.status = 'confirmado')::int as arrived_count,
                        bool_or(mp.user_id = cast(:user_id as uuid) and mp.status = 'confirmado') as current_user_confirmed
                    from public.match_participants_v2 mp
                    where mp.match_id in (select id from candidate_matches)
                    group by mp.match_id
                ),
                guest_presence as (
                    select
                        mg.match_id,
                        count(*)::int as guests_count,
                        count(*) filter (where mg.has_arrived is true and mg.status = 'confirmado')::int as arrived_guest_count
                    from public.match_guests_v2 mg
                    where mg.match_id in (select id from candidate_matches)
                    group by mg.match_id
                )
                select
                    m.id::text as id,
                    m.group_id::text as group_id,
                    g.name as group_name,
                    m.title,
                    m.status::text as status,
                    m.starts_at,
                    m.ends_at,
                    m.location_name,
                    m.city,
                    coalesce(p.confirmed_count, 0)::int as confirmed_count,
                    coalesce(p.waiting_count, 0)::int as waiting_count,
                    coalesce(gp.guests_count, 0)::int as guests_count,
                    (coalesce(p.arrived_count, 0) + coalesce(gp.arrived_guest_count, 0))::int as arrived_count,
                    coalesce(p.current_user_confirmed, false) as is_current_user_confirmed,
                    m.draw_status::text as draw_status
                from candidate_matches m
                join public.groups g on g.id = m.group_id
                left join presence p on p.match_id = m.id
                left join guest_presence gp on gp.match_id = m.id
                order by
                    case when coalesce(p.current_user_confirmed, false) then 0 else 1 end,
                    m.starts_at asc,
                    m.created_at asc
                limit :limit
            """),
            {"user_id": user_id, "limit": int(limit)},
        ).mappings().all()
        return [dict(row) for row in rows]

    def _fetch_notifications(self, db: Session, *, user_id: str) -> dict[str, Any]:
        row = db.execute(
            text("""
                select
                    count(*) filter (where is_read = false)::int as unread_count,
                    max(created_at) as latest_created_at
                from public.notification_events_v2
                where recipient_user_id = cast(:user_id as uuid)
            """),
            {"user_id": user_id},
        ).mappings().first()
        return dict(row) if row else {"unread_count": 0, "latest_created_at": None}
