from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class RankingV2Repository:
    def fetch_membership(self, db: Session, *, group_id: str, user_id: str) -> dict[str, Any] | None:
        row = db.execute(text("""
            select gm.id::text as membership_id, gm.role::text as role, gm.status::text as status
            from public.group_members gm
            join public.players p on p.id = gm.player_id
            where gm.group_id = cast(:group_id as uuid)
              and p.user_id = cast(:user_id as uuid)
            limit 1
        """), {'group_id': group_id, 'user_id': user_id}).mappings().first()
        return dict(row) if row else None

    def list_group_ranking(self, db: Session, *, group_id: str, period_days: int | None) -> list[dict[str, Any]]:
        params = {'group_id': group_id, 'period_days': period_days}
        sql = """
            select
                p.id::text as player_id,
                u.id::text as user_id,
                coalesce(nullif(trim(concat_ws(' ', nullif(trim(u.first_name),''), nullif(trim(u.last_name),''))), ''), nullif(trim(p.display_name),''), nullif(trim(p.full_name),''), nullif(trim(u.name),''), 'Jogador') as display_name,
                p.avatar_url,
                count(distinct s.match_id)::int as games,
                coalesce(sum(s.goals), 0)::int as goals,
                coalesce(sum(s.assists), 0)::int as assists,
                coalesce(sum(s.own_goals), 0)::int as own_goals,
                coalesce(sum(s.yellow_cards), 0)::int as yellow_cards,
                coalesce(sum(s.red_cards), 0)::int as red_cards,
                coalesce(sum(s.wins), 0)::int as wins,
                0::int as draws,
                0::int as losses,
                coalesce(sum(s.fair_play), 0)::int as fair_play,
                max(m.finished_at) as last_match_at
            from public.match_player_stats_v2 s
            join public.matches_v2 m on m.id = s.match_id
            join public.players p on p.id = s.player_id
            left join public.users u on u.id = p.user_id
            join public.group_members gm on gm.group_id = cast(:group_id as uuid) and gm.player_id = p.id and gm.status = cast('active' as membership_status_enum)
            where m.group_id = cast(:group_id as uuid)
              and m.status = 'finished'
              and s.player_id is not null
              and (:period_days is null or m.finished_at >= now() - make_interval(days => :period_days))
            group by p.id, u.id, p.display_name, p.full_name, p.avatar_url, u.first_name, u.last_name, u.name
            order by wins desc, games desc, goals desc, assists desc
        """
        rows = db.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]
