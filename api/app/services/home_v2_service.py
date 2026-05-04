from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.supabase_auth import SupabasePrincipal
from app.schemas.home_v2 import HomeSummaryV2Model
from app.services.groups_v2_service import GroupsV2Service
from app.services.matches_v2_service import MatchesV2Service
from app.services.profile_v2_service import ProfileV2Service
from app.routes.social_v2_extended import get_player_profile, get_player_reputation


class HomeV2Service:
    """Aggregated, read-only payload for the mobile Home.

    Guardrail: this service must not create/update financial, presence, match,
    notification or social entities. It only consolidates data already exposed by
    the existing read services to remove multiple visible mobile requests.
    """

    def __init__(self) -> None:
        self.profile_service = ProfileV2Service()
        self.groups_service = GroupsV2Service()
        self.matches_service = MatchesV2Service()

    @staticmethod
    def _dump_model(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return value
        return dict(value)

    @staticmethod
    def _match_item(group: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
        confirmed_count = int(match.get("confirmed_count") or 0) + int(match.get("guests_count") or 0)
        player_limit = int(match.get("line_slots") or 0) + int(match.get("goalkeeper_slots") or 0)
        return {
            "group": group,
            "match": match,
            "is_confirmed": bool(match.get("is_current_user_confirmed")),
            "confirmed_count": confirmed_count,
            "waiting_count": int(match.get("waiting_count") or 0),
            "available_spots": max(0, player_limit - confirmed_count),
        }

    def get_summary(self, db: Session, principal: SupabasePrincipal) -> HomeSummaryV2Model:
        profile_model = self.profile_service.get_me(db, principal)
        profile = self._dump_model(profile_model)
        player_id = str(profile.get("player_id_str") or profile.get("player_id") or "").strip()

        social_profile: dict[str, Any] | None = None
        reputation: dict[str, Any] | None = None
        if player_id:
            try:
                social_profile = self._dump_model(get_player_profile(player_id, principal, db))
            except Exception:
                social_profile = None
            try:
                reputation = self._dump_model(get_player_reputation(player_id, principal, db))
            except Exception:
                reputation = None

        groups = [self._dump_model(g) for g in self.groups_service.list_my_groups(db, principal)]
        now = datetime.now(timezone.utc)
        week_end = now + timedelta(days=7)

        best: dict[str, Any] | None = None
        upcoming: list[dict[str, Any]] = []

        for group in groups:
            group_id = str(group.get("id") or "")
            if not group_id:
                continue
            try:
                matches = [self._dump_model(m) for m in self.matches_service.list_group_matches(db, principal, group_id)]
            except Exception:
                matches = []

            future_matches: list[dict[str, Any]] = []
            for match in matches:
                status = str(match.get("status") or "").strip().lower()
                if status in {"finished", "cancelled", "canceled"}:
                    continue
                starts_raw = match.get("starts_at")
                ends_raw = match.get("ends_at") or starts_raw
                try:
                    starts_at = datetime.fromisoformat(str(starts_raw).replace("Z", "+00:00"))
                    ends_at = datetime.fromisoformat(str(ends_raw).replace("Z", "+00:00"))
                except Exception:
                    continue
                if starts_at.tzinfo is None:
                    starts_at = starts_at.replace(tzinfo=timezone.utc)
                if ends_at.tzinfo is None:
                    ends_at = ends_at.replace(tzinfo=timezone.utc)
                if ends_at <= now:
                    continue
                match["_starts_at_dt"] = starts_at
                future_matches.append(match)

            future_matches.sort(key=lambda item: item.get("_starts_at_dt") or now)
            for match in future_matches:
                item = self._match_item(group, {k: v for k, v in match.items() if k != "_starts_at_dt"})
                starts_at = match.get("_starts_at_dt") or now
                if starts_at < week_end:
                    upcoming.append(item)
                if item["is_confirmed"]:
                    if best is None:
                        best = item
                    else:
                        current_best = datetime.fromisoformat(str(best["match"].get("starts_at")).replace("Z", "+00:00"))
                        if starts_at < current_best:
                            best = item

        upcoming.sort(key=lambda item: str(item.get("match", {}).get("starts_at") or ""))

        return HomeSummaryV2Model(
            profile=profile,
            social_profile=social_profile,
            reputation=reputation,
            next_confirmed_match=best,
            upcoming_week=upcoming[:3],
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
