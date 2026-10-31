from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
import secrets
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from pydantic import BaseModel, Field, StrictBool, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.db import get_session_local
from app.core.supabase_auth import SupabasePrincipal, get_current_supabase_principal
from app.services.platform_communication_service import PlatformCommunicationService

router = APIRouter(tags=['Canal BoraFut'])
service = PlatformCommunicationService()


def get_db():
    db = get_session_local()()
    try:
        yield db
    except SQLAlchemyError:
        try:
            db.rollback()
        except SQLAlchemyError:
            pass
        # SQL exceptions can contain message bodies in their bound parameters.
        # Do not allow the generic error logger to receive those exceptions.
        raise HTTPException(503, 'Canal temporariamente indisponível. Tente novamente.') from None
    finally:
        try:
            db.close()
        except SQLAlchemyError:
            pass


class MessageIn(BaseModel):
    request_key: UUID
    body: str = Field(..., min_length=1, max_length=4000)

    @field_validator('body')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Mensagem vazia')
        return value.strip()


class ThreadIn(MessageIn):
    subject: str = Field(..., min_length=1, max_length=120)
    category: Literal['question','problem','suggestion']

    @field_validator('subject')
    @classmethod
    def subject_nonempty(cls, value):
        if not value.strip():
            raise ValueError('Assunto vazio')
        return value.strip()


class StateIn(BaseModel):
    status: Literal['open','in_progress','resolved']


class PreferenceIn(BaseModel):
    promotional_opt_in: StrictBool


class DeviceIn(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=200)
    push_token: str = Field(..., min_length=1, max_length=4096)
    platform: Literal['ios','android','web','other']
    is_active: StrictBool = True
    locale: Literal['pt-BR','pt-PT','en-US','es-ES'] = 'pt-BR'


class DeviceOffIn(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=200)


class CampaignIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    body: str = Field(..., min_length=1, max_length=1200)
    kind: Literal['promotional','announcement'] = 'promotional'
    audience: Literal['all','group_managers'] = 'all'
    locale: Literal['all','pt-BR','pt-PT','en-US','es-ES'] = 'all'
    destination: Literal['communications','home'] = 'communications'
    scheduled_at: datetime | None = None

    @field_validator('title','body')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Conteúdo vazio')
        return value.strip()

    @field_validator('scheduled_at')
    @classmethod
    def timezone_required(cls, value):
        if value and value.tzinfo is None:
            raise ValueError('Informe o fuso horário do agendamento')
        if value and value <= datetime.now(timezone.utc):
            raise ValueError('O agendamento precisa estar no futuro')
        return value


class ConfirmIn(BaseModel):
    confirm: StrictBool


@router.get('/v2/support/threads')
def own_threads(limit: int = Query(50,ge=1,le=100), offset: int = Query(0,ge=0),
                principal: SupabasePrincipal = Depends(get_current_supabase_principal), db=Depends(get_db)):
    return service.threads(db,user_id=principal.user_id,limit=limit,offset=offset)


@router.post('/v2/support/threads',status_code=201)
def create_thread(payload: ThreadIn, principal: SupabasePrincipal = Depends(get_current_supabase_principal), db=Depends(get_db)):
    result=service.create_thread(db,user_id=principal.user_id,payload=payload)
    db.commit()
    return result


@router.get('/v2/support/threads/{thread_id}/messages')
def own_messages(thread_id: UUID, before: int | None = Query(None,ge=1),
                 principal: SupabasePrincipal = Depends(get_current_supabase_principal), db=Depends(get_db)):
    return service.messages(db,user_id=principal.user_id,thread_id=thread_id,before=before)


@router.post('/v2/support/threads/{thread_id}/messages',status_code=201)
def user_reply(thread_id: UUID,payload: MessageIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal), db=Depends(get_db)):
    result=service.add_message(db,user_id=principal.user_id,thread_id=thread_id,payload=payload)
    db.commit()
    return result


@router.get('/v2/communications/preferences')
def preferences(principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    return service.preferences(db,principal.user_id)


@router.put('/v2/communications/preferences')
def set_preferences(payload: PreferenceIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.preferences(db,principal.user_id,payload.promotional_opt_in)
    db.commit()
    return result


@router.get('/v2/communications/campaigns')
def own_campaigns(limit: int = Query(50,ge=1,le=100),offset: int = Query(0,ge=0),
                  principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    return service.campaigns(db,principal.user_id,limit=limit,offset=offset)


@router.put('/v2/push/device-tokens')
def register_device(payload: DeviceIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.register_device(db,principal.user_id,payload)
    db.commit()
    return result


@router.get('/v2/communications/campaigns/{campaign_id}')
def own_campaign(campaign_id: UUID,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    rows=service.campaigns(db,principal.user_id,campaign_id=campaign_id,limit=1)
    if not rows:
        raise HTTPException(404,'Comunicado não disponível para este utilizador.')
    return rows[0]


@router.post('/v2/push/device-tokens/deactivate')
def deactivate_device(payload: DeviceOffIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.deactivate_device(db,principal.user_id,payload.device_id)
    db.commit()
    return result


@router.get('/v2/platform-admin/config')
def admin_public_config():
    from jose import jwt, JWTError
    key=settings.supabase_anon_key
    # This is a disclosure guard for a public API key, never authentication.
    public_key=key.startswith('sb_publishable_')
    if not public_key:
        try:
            public_key=jwt.get_unverified_claims(key).get('role')=='anon'
        except (JWTError,ValueError,TypeError):
            public_key=False
    project=urlparse(settings.supabase_url)
    if not public_key or project.scheme!='https' or not project.hostname or project.username or project.password:
        raise HTTPException(503,'Configuração pública de autenticação indisponível.')
    return {'supabase_url':settings.supabase_url,'supabase_anon_key':settings.supabase_anon_key}


@router.get('/v2/platform-admin/me')
def staff_me(principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    return service.staff(db,principal.user_id)


@router.get('/v2/platform-admin/threads')
def staff_threads(status: Literal['open','in_progress','resolved'] | None=None,
                   limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),
                   principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    return service.threads(db,user_id=principal.user_id,staff=True,status=status,limit=limit,offset=offset)


@router.get('/v2/platform-admin/threads/{thread_id}/messages')
def staff_messages(thread_id: UUID,before: int | None=Query(None,ge=1),
                    principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    return service.messages(db,user_id=principal.user_id,thread_id=thread_id,staff=True,before=before)


@router.post('/v2/platform-admin/threads/{thread_id}/messages',status_code=201)
def staff_reply(thread_id: UUID,payload: MessageIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.add_message(db,user_id=principal.user_id,thread_id=thread_id,payload=payload,staff=True)
    db.commit()
    return result


@router.patch('/v2/platform-admin/threads/{thread_id}')
def staff_status(thread_id: UUID,payload: StateIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.set_status(db,user_id=principal.user_id,thread_id=thread_id,status=payload.status)
    db.commit()
    return result


@router.get('/v2/platform-admin/campaigns')
def staff_campaigns(limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0),
                     principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    return service.campaigns(db,principal.user_id,staff=True,limit=limit,offset=offset)


@router.post('/v2/platform-admin/campaigns',status_code=201)
def create_campaign(payload: CampaignIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.create_campaign(db,principal.user_id,payload)
    db.commit()
    return result


@router.post('/v2/platform-admin/campaigns/{campaign_id}/publish')
def publish_campaign(campaign_id: UUID,payload: ConfirmIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.publish_campaign(db,principal.user_id,campaign_id,confirm=payload.confirm)
    db.commit()
    return result


@router.put('/v2/platform-admin/campaigns/{campaign_id}')
def update_campaign(campaign_id: UUID,payload: CampaignIn,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.update_campaign(db,principal.user_id,campaign_id,payload)
    db.commit()
    return result


@router.post('/v2/platform-admin/campaigns/{campaign_id}/cancel')
def cancel_campaign(campaign_id: UUID,principal: SupabasePrincipal = Depends(get_current_supabase_principal),db=Depends(get_db)):
    result=service.cancel_campaign(db,principal.user_id,campaign_id)
    db.commit()
    return result


@router.post('/internal/communication-worker')
def run_worker(authorization: str | None=Header(None),db=Depends(get_db)):
    expected=settings.communication_worker_secret
    if len(expected)<32 or not authorization or not secrets.compare_digest(authorization,'Bearer '+expected):
        raise HTTPException(401,'Credencial de execução inválida.')
    from app.services.communication_worker import CommunicationWorker
    return CommunicationWorker().run(db)
