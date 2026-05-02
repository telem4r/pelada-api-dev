from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.schemas.matches_v2 import (
    MatchCreateV2Request,
    MatchDrawBaseV2Model,
    MatchDrawGenerateV2Request,
    MatchDrawResultV2Model,
    MatchEventCreateV2Request,
    MatchGameFlowV2Model,
    MatchGuestCreateV2Request,
    MatchGuestV2Model,
    MatchOperationLocksV2Request,
    MatchPostStatsV2Request,
    MatchUpdateV2Request,
    MatchPresenceUpsertV2Request,
    MatchPresenceV2Model,
    MatchStatsSummaryV2Model,
    MatchSummaryV2Model,
)
from app.services.matches_v2_service import MatchesV2Service

router = APIRouter(prefix='/v2/groups/{group_id}/matches', tags=['Matches V2'])
service = MatchesV2Service()

UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')


def get_db_session():
    SessionLocal = get_session_local()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@router.get('', response_model=list[MatchSummaryV2Model])
def list_matches(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_group_matches(db, principal, group_id)


@router.post('', response_model=MatchSummaryV2Model)
def create_match(group_id: str, payload: MatchCreateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.create_match(db, principal, group_id, payload)


@router.get('/history')
def get_matches_history(group_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    identity = service._identity_or_404(db, principal)
    service._require_active_membership(db, group_id=group_id, user_id=identity['user_id'])
    rows = db.execute(text("""
        with scores as (
            select e.match_id,
                   e.team_number,
                   count(*) filter (where e.event_type = 'goal') as goals
            from public.match_events_v2 e
            join public.matches_v2 m on m.id = e.match_id
            where m.group_id = cast(:gid as uuid)
              and e.team_number > 0
            group by e.match_id, e.team_number
        )
        select m.id::text as match_id,
               m.starts_at,
               m.ends_at,
               m.title,
               m.status::text as status,
               coalesce((select goals from scores where scores.match_id = m.id and scores.team_number = 1), 0)::int as team1,
               coalesce((select goals from scores where scores.match_id = m.id and scores.team_number = 2), 0)::int as team2,
               case when coalesce((select goals from scores where scores.match_id = m.id and scores.team_number = 1), 0) >
                         coalesce((select goals from scores where scores.match_id = m.id and scores.team_number = 2), 0)
                    then 1
                    when coalesce((select goals from scores where scores.match_id = m.id and scores.team_number = 2), 0) >
                         coalesce((select goals from scores where scores.match_id = m.id and scores.team_number = 1), 0)
                    then 2
                    else null end as winner_team,
               (select coalesce(
                    nullif(trim(concat_ws(' ', nullif(trim(u2.first_name),''), nullif(trim(u2.last_name),''))), ''),
                    nullif(trim(p2.display_name),''),
                    nullif(trim(p2.full_name),''),
                    nullif(trim(u2.name),''),
                    'Jogador'
                )
                from public.match_player_stats_v2 mps
                join public.players p2 on p2.id = mps.player_id
                left join public.users u2 on u2.id = p2.user_id
                where mps.match_id = m.id and mps.mvp = true
                limit 1) as mvp_name
        from public.matches_v2 m
        where m.group_id = cast(:gid as uuid) and m.status = 'finished'
        order by m.starts_at desc limit 100
    """), {'gid': group_id}).mappings().all()
    return [dict(r) for r in rows]


@router.get('/{match_id}', response_model=MatchSummaryV2Model)
def get_match(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    if not UUID_RE.match(match_id):
        raise HTTPException(status_code=404, detail='Partida não encontrada.')
    return service.get_match(db, principal, group_id, match_id)


@router.get('/{match_id}/presence', response_model=MatchPresenceV2Model)
def get_presence(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_presence(db, principal, group_id, match_id)


@router.post('/{match_id}/presence', response_model=MatchPresenceV2Model)
def upsert_presence(group_id: str, match_id: str, payload: MatchPresenceUpsertV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.upsert_presence(db, principal, group_id, match_id, payload)


@router.delete('/{match_id}/presence', response_model=MatchPresenceV2Model)
def remove_presence(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.remove_presence(db, principal, group_id, match_id)


@router.post('/{match_id}/presence/arrival', response_model=MatchPresenceV2Model)
def mark_self_arrival(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_self_arrival(db, principal, group_id, match_id, True)


@router.delete('/{match_id}/presence/arrival', response_model=MatchPresenceV2Model)
def unmark_self_arrival(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_self_arrival(db, principal, group_id, match_id, False)


@router.get('/{match_id}/guests', response_model=list[MatchGuestV2Model])
def list_guests(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.list_guests(db, principal, group_id, match_id)


@router.post('/{match_id}/guests', response_model=MatchGuestV2Model)
def create_guest(group_id: str, match_id: str, payload: MatchGuestCreateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.create_guest(db, principal, group_id, match_id, payload)


@router.delete('/{match_id}/guests/{guest_id}')
def delete_guest(group_id: str, match_id: str, guest_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.delete_guest(db, principal, group_id, match_id, guest_id)


@router.post('/{match_id}/guests/{guest_id}/promote', response_model=MatchPresenceV2Model)
def promote_guest(group_id: str, match_id: str, guest_id: str, payload: dict = Body(default={}), principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    target_position = None
    if isinstance(payload, dict):
        target_position = payload.get('position')
    return service.promote_guest_presence(db, principal, group_id, match_id, guest_id, target_position)


@router.post('/{match_id}/guests/{guest_id}/arrival', response_model=MatchPresenceV2Model)
def mark_guest_arrival(group_id: str, match_id: str, guest_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_guest_arrival(db, principal, group_id, match_id, guest_id, True)


@router.delete('/{match_id}/guests/{guest_id}/arrival', response_model=MatchPresenceV2Model)
def unmark_guest_arrival(group_id: str, match_id: str, guest_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.mark_guest_arrival(db, principal, group_id, match_id, guest_id, False)


@router.get('/{match_id}/draw/base', response_model=MatchDrawBaseV2Model)
def get_draw_base(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_draw_base(db, principal, group_id, match_id)


@router.post('/{match_id}/draw/generate', response_model=MatchDrawResultV2Model)
def generate_draw(group_id: str, match_id: str, payload: MatchDrawGenerateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.generate_draw(db, principal, group_id, match_id, payload)


@router.get('/{match_id}/draw/result', response_model=MatchDrawResultV2Model)
def get_draw_result(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_saved_draw(db, principal, group_id, match_id)


@router.patch('/{match_id}/settings', response_model=MatchSummaryV2Model)
def update_match_settings(group_id: str, match_id: str, payload: MatchUpdateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.update_match_settings(db, principal, group_id, match_id, payload)


@router.post('/{match_id}/start', response_model=MatchSummaryV2Model)
def start_match(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.start_match(db, principal, group_id, match_id)


@router.post('/{match_id}/finish', response_model=MatchSummaryV2Model)
def finish_match(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.finish_match(db, principal, group_id, match_id)


@router.get('/{match_id}/game-flow', response_model=MatchGameFlowV2Model)
def get_game_flow(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_game_flow(db, principal, group_id, match_id)


@router.get('/{match_id}/stats', response_model=MatchStatsSummaryV2Model)
def get_match_stats(group_id: str, match_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.get_match_stats(db, principal, group_id, match_id)


@router.post('/{match_id}/stats')
def submit_post_match_stats(group_id: str, match_id: str, payload: MatchPostStatsV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.submit_post_match_stats(db, principal, group_id, match_id, payload)


@router.post('/{match_id}/events', response_model=MatchGameFlowV2Model)
def create_match_event(group_id: str, match_id: str, payload: MatchEventCreateV2Request, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.create_match_event(db, principal, group_id, match_id, payload)


@router.delete('/{match_id}/events/{event_id}', response_model=MatchGameFlowV2Model)
def delete_match_event(group_id: str, match_id: str, event_id: str, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db: Session = Depends(get_db_session)):
    return service.delete_match_event(db, principal, group_id, match_id, event_id)
