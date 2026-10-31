from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.schemas.matches_v2 import MatchCreateV2Request
from app.services.matches_v2_service import MatchesV2Service


class CloneMatchRequest(BaseModel):
    source_match_id: UUID | None = None
    request_key: UUID | None = None
    time_zone: str | None = Field(None, max_length=100)
    mode: str = Field('manual', pattern='^(manual|next_weekday)$')
    local_starts_at: datetime | None = None
    local_ends_at: datetime | None = None
    # Preserve the existing installed app's UTC date contract.
    starts_at: datetime | None = None
    ends_at: datetime | None = None


def checked_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise HTTPException(422, 'Escolha um fuso horário válido para a partida.') from exc


def local_to_utc(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is not None:
        raise HTTPException(422, 'Informe o horário local sem deslocamento; o fuso é informado separadamente.')
    variants = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(zone).replace(tzinfo=None) == value and candidate not in variants:
            variants.append(candidate)
    if not variants:
        raise HTTPException(422, 'Esse horário não existe devido à mudança de horário de verão. Escolha outro horário.')
    if len(variants) > 1:
        raise HTTPException(422, 'Esse horário ocorre duas vezes na mudança de horário de verão. Escolha um horário sem ambiguidade.')
    return variants[0]


def next_occurrence(starts: datetime, ends: datetime, zone_name: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    zone = checked_zone(zone_name)
    now = now or datetime.now(timezone.utc)
    local_start = starts.astimezone(zone).replace(tzinfo=None)
    local_end = ends.astimezone(zone).replace(tzinfo=None)
    duration = local_end - local_start
    if duration <= timedelta(0):
        raise HTTPException(422, 'A partida de referência não tem duração válida.')
    today = now.astimezone(zone).date()
    days = (local_start.weekday() - today.weekday()) % 7 or 7
    proposed = datetime.combine(today + timedelta(days=days), local_start.time())
    return local_to_utc(proposed, zone), local_to_utc(proposed + duration, zone)


class MatchTemplateService:
    def __init__(self):
        self.matches = MatchesV2Service()

    def source(self, db, *, group_id, source_match_id=None) -> dict:
        row = db.execute(text('''
            select * from public.matches_v2
            where group_id=cast(:group_id as uuid) and status::text not in ('cancelled','canceled','cancelada')
              and (cast(:source_id as uuid) is null or id=cast(:source_id as uuid))
            order by (starts_at <= now()) desc, starts_at desc, created_at desc, id desc
            limit 1 for share
        '''), {'group_id': group_id, 'source_id': str(source_match_id) if source_match_id else None}).mappings().first()
        if not row:
            raise HTTPException(404, 'Não há partida de referência disponível neste grupo.')
        return dict(row)

    def proposal(self, db, principal, group_id, *, source_match_id=None, time_zone=None):
        identity = self.matches._identity_or_404(db, principal)
        self.matches._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        source = self.source(db, group_id=group_id, source_match_id=source_match_id)
        zone_name = time_zone or source.get('time_zone')
        if not zone_name:
            raise HTTPException(422, 'Escolha o fuso horário da partida para calcular a próxima data.')
        starts, ends = next_occurrence(source['starts_at'], source['ends_at'], zone_name)
        zone = checked_zone(zone_name)
        return {'source_match_id': str(source['id']), 'source_title': source.get('title'), 'time_zone': zone_name,
                'starts_at': starts, 'ends_at': ends,
                'local_starts_at': starts.astimezone(zone).replace(tzinfo=None).isoformat(),
                'local_ends_at': ends.astimezone(zone).replace(tzinfo=None).isoformat()}

    def create(self, db, principal, group_id, payload: CloneMatchRequest):
        identity = self.matches._identity_or_404(db, principal)
        self.matches._require_admin(db, group_id=group_id, user_id=identity['user_id'])
        fingerprint = hashlib.sha256(json.dumps(payload.model_dump(mode='json', exclude={'request_key'}), sort_keys=True).encode()).hexdigest()
        if payload.request_key:
            db.execute(text('select pg_advisory_xact_lock(hashtextextended(:scope,0))'),
                       {'scope': f'match-clone:{principal.user_id}:{group_id}:{payload.request_key}'})
            saved = db.execute(text('''select match_id::text, fingerprint from public.match_clone_requests_v2
                where user_id=cast(:user_id as uuid) and group_id=cast(:group_id as uuid) and request_key=cast(:key as uuid)
            '''), {'user_id': principal.user_id, 'group_id': group_id, 'key': str(payload.request_key)}).mappings().first()
            if saved:
                if saved['fingerprint'] != fingerprint:
                    raise HTTPException(409, 'Esta confirmação já foi usada com outros dados. Revise a nova partida.')
                if not saved['match_id']:
                    raise HTTPException(410, 'A partida dessa confirmação já foi removida.')
                return self.matches.get_match(db, principal, group_id, saved['match_id'])
        source = self.source(db, group_id=group_id, source_match_id=payload.source_match_id)
        zone_name = payload.time_zone or source.get('time_zone')
        if payload.local_starts_at or payload.local_ends_at:
            if not payload.local_starts_at or not payload.local_ends_at or not zone_name:
                raise HTTPException(422, 'Informe início, fim e fuso horário.')
            zone = checked_zone(zone_name)
            starts = local_to_utc(payload.local_starts_at, zone)
            ends = local_to_utc(payload.local_ends_at, zone)
        elif payload.mode == 'next_weekday':
            if not zone_name:
                raise HTTPException(422, 'Escolha o fuso horário da partida.')
            starts, ends = next_occurrence(source['starts_at'], source['ends_at'], zone_name)
        else:
            starts, ends = payload.starts_at, payload.ends_at
            if not starts or not ends or starts.tzinfo is None or ends.tzinfo is None:
                raise HTTPException(422, 'Informe início e fim com fuso horário.')
            if zone_name:
                checked_zone(zone_name)
        if starts <= datetime.now(timezone.utc) or ends <= starts:
            raise HTTPException(422, 'A nova partida precisa ter início futuro e término posterior ao início.')
        copy_fields = ('title','location_name','city','notes','line_slots','goalkeeper_slots','price_cents',
                       'currency','payment_method','payment_key','single_waitlist_release_days','modality','gender_type','is_public')
        copied = {field: source[field] for field in copy_fields if field in source and source[field] is not None}
        request = MatchCreateV2Request(**copied, starts_at=starts, ends_at=ends)
        created = self.matches.create_match(db, principal, group_id, request, commit=False)
        db.execute(text('update public.matches_v2 set time_zone=:zone where id=cast(:id as uuid)'), {'zone': zone_name, 'id': created.id})
        if payload.request_key:
            db.execute(text('''insert into public.match_clone_requests_v2(user_id,group_id,request_key,fingerprint,match_id,source_match_id)
                values(cast(:user_id as uuid),cast(:group_id as uuid),cast(:key as uuid),:fingerprint,cast(:match_id as uuid),cast(:source_id as uuid))
            '''), {'user_id': principal.user_id, 'group_id': group_id, 'key': str(payload.request_key),
                   'fingerprint': fingerprint, 'match_id': created.id, 'source_id': str(source['id'])})
        db.commit()
        self.matches._invalidate_group_cache(group_id=group_id, match_id=created.id)
        created.time_zone = zone_name
        return created
