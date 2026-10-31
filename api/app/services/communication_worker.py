from __future__ import annotations

import json
from threading import Lock

from sqlalchemy import text

from app.core.config import settings
from app.services.platform_communication_service import PlatformCommunicationService


class PushFailure(Exception):
    def __init__(self, code, retryable=False, deactivate=False):
        self.code, self.retryable, self.deactivate = code, retryable, deactivate


class FcmSender:
    def __init__(self):
        self.session = None
        self.project = None
        self.lock = Lock()

    def send(self, *, token, title, body, data, delivery_id):
        # Authentication uses Google's maintained OAuth implementation; no
        # service-account credentials are exposed to mobile/web clients.
        if self.session is None:
            with self.lock:
                if self.session is None:
                    from google.oauth2 import service_account
                    from google.auth.transport.requests import AuthorizedSession
                    try:
                        info=json.loads(settings.firebase_service_account_json)
                        credentials=service_account.Credentials.from_service_account_info(info,
                            scopes=['https://www.googleapis.com/auth/firebase.messaging'])
                        self.project=info['project_id']
                        self.session=AuthorizedSession(credentials,max_refresh_attempts=1,refresh_timeout=5)
                    except Exception:
                        raise PushFailure('provider_configuration') from None
        try:
            response=self.session.post(f'https://fcm.googleapis.com/v1/projects/{self.project}/messages:send',
                json={'message':{'token':token,'notification':{'title':title,'body':body},
                    'data':{**data,'delivery_id':delivery_id},
                    'android':{'notification':{'tag':delivery_id}},
                    'apns':{'headers':{'apns-collapse-id':delivery_id}}}},timeout=5)
        except Exception:
            raise PushFailure('provider_network',retryable=True) from None
        if response.status_code==200:
            try:
                return response.json()['name']
            except (ValueError,KeyError):
                raise PushFailure('provider_invalid_response',retryable=True) from None
        code='provider_http_'+str(response.status_code)
        try:
            invalid=any(d.get('errorCode')=='UNREGISTERED' for d in response.json().get('error',{}).get('details',[]))
        except (ValueError,TypeError,AttributeError):
            invalid=False
        raise PushFailure(code,retryable=response.status_code==429 or response.status_code>=500,deactivate=invalid)


_sender=FcmSender()


class CommunicationWorker:
    def __init__(self, sender=None):
        self.sender=sender or _sender

    def expand_due(self, db):
        campaign=db.execute(text('''select * from public.platform_campaigns_v2
            where status='scheduled' and scheduled_at<=now() order by scheduled_at
            for update skip locked limit 1''')).mappings().first()
        if not campaign:
            return None
        db.execute(text('''insert into public.campaign_recipients_v2(campaign_id,user_id)
            select cast(:id as uuid),u.id from public.users u
            left join public.communication_preferences_v2 p on p.user_id=u.id
            left join public.notification_settings_v2 s on s.user_id=u.id
            where coalesce(s.announcements_enabled,true)
              and (:kind<>'promotional' or coalesce(p.promotional_opt_in,false))
              and (:audience='all' or exists(select 1 from public.group_members gm join public.players pl on pl.id=gm.player_id
                where pl.user_id=u.id and gm.status::text='active' and gm.role::text in ('owner','admin')))
              and (:locale='all' or exists(select 1 from public.device_tokens_v2 d
                where d.user_id=u.id and d.is_active and d.locale=:locale))
            on conflict do nothing'''), {'id':str(campaign['id']),'kind':campaign['kind'],
                'audience':campaign['audience'],'locale':campaign['locale']})
        db.execute(text('''insert into public.notification_events_v2(id,recipient_user_id,actor_user_id,event_type,title,message,payload,is_read)
            select r.notification_id,r.user_id,c.created_by_user_id,'platform_campaign',c.title,c.body,
              jsonb_build_object('kind','campaign','campaign_id',c.id::text),false
            from public.campaign_recipients_v2 r join public.platform_campaigns_v2 c on c.id=r.campaign_id
            where c.id=cast(:id as uuid) on conflict(id) do nothing'''), {'id':str(campaign['id'])})
        db.execute(text('''insert into public.push_outbox_v2(user_id,device_id,campaign_id)
            select r.user_id,d.device_id,r.campaign_id from public.campaign_recipients_v2 r
            join public.device_tokens_v2 d on d.user_id=r.user_id and d.is_active and d.push_token is not null
            where r.campaign_id=cast(:id as uuid) on conflict do nothing'''), {'id':str(campaign['id'])})
        db.execute(text("update public.platform_campaigns_v2 set status='queued',updated_at=now() where id=cast(:id as uuid)"), {'id':str(campaign['id'])})
        return str(campaign['id'])

    def run(self, db, *, batch_size=10, allow_send=None):
        result=PlatformCommunicationService().purge_expired(db)
        result['campaign_expanded']=self.expand_due(db)
        db.commit()
        enabled=settings.push_delivery_enabled if allow_send is None else allow_send
        result.update(push_delivery_enabled=enabled,processed=0)
        if not enabled:
            return result
        db.execute(text("""update public.push_outbox_v2 set state='failed',last_error_code='lease_exhausted'
            where state='processing' and lease_until<now() and attempts>=5"""))
        jobs=db.execute(text('''update public.push_outbox_v2 set state='processing',attempts=attempts+1,
            lease_until=now()+interval '2 minutes'
            where id in(select id from public.push_outbox_v2
                where attempts<5 and ((state in ('pending','retry') and available_at<=now())
                  or (state='processing' and lease_until<now()))
                order by created_at for update skip locked limit :batch)
            returning id::text'''), {'batch':max(1,min(batch_size,10))}).mappings().all()
        db.commit()
        for item in jobs:
            self.deliver(db,item['id'])
            db.commit()
            result['processed']+=1
        db.execute(text("""update public.platform_campaigns_v2 c set status='sent',updated_at=now()
            where c.status='queued' and not exists(select 1 from public.push_outbox_v2 j
                where j.campaign_id=c.id and j.state in ('pending','processing','retry'))"""))
        db.commit()
        return result

    def deliver(self,db,identifier):
        row=db.execute(text('''select j.id::text,j.user_id::text,j.device_id,j.attempts,
            j.message_id::text,j.campaign_id::text,d.push_token,d.locale,d.is_active,
            d.user_id::text as current_device_user,
            c.title,c.body,c.kind,c.status as campaign_status,c.destination,
            m.thread_id::text,m.expires_at,
            coalesce(s.push_enabled,true) as push_enabled,
            coalesce(s.push_announcements_enabled,true) as push_announcements,
            coalesce(p.promotional_opt_in,false) as promotional_opt_in
            from public.push_outbox_v2 j join public.device_tokens_v2 d on d.device_id=j.device_id
            left join public.platform_campaigns_v2 c on c.id=j.campaign_id
            left join public.support_messages_v2 m on m.id=j.message_id
            left join public.notification_settings_v2 s on s.user_id=j.user_id
            left join public.communication_preferences_v2 p on p.user_id=j.user_id
            where j.id=cast(:id as uuid) and j.state='processing' for update of j'''), {'id':identifier}).mappings().first()
        if not row:
            return
        cancel=(not row['is_active'] or not row['push_token'] or row['current_device_user']!=row['user_id'] or not row['push_enabled'])
        if row['campaign_id']:
            cancel=cancel or row['campaign_status'] not in ('queued','sent') or not row['push_announcements']
            cancel=cancel or (row['kind']=='promotional' and not row['promotional_opt_in'])
            # One commercial notification per user per day, across campaigns/devices.
            if not cancel and row['kind']=='promotional':
                db.execute(text('select pg_advisory_xact_lock(hashtextextended(:scope,0))'), {'scope':'promo-frequency:'+row['user_id']})
                recent=db.execute(text('''select exists(select 1 from public.push_outbox_v2 j
                    join public.platform_campaigns_v2 c on c.id=j.campaign_id
                    where j.user_id=cast(:uid as uuid) and j.state='sent' and c.kind='promotional'
                      and j.campaign_id<>cast(:cid as uuid) and j.sent_at>now()-interval '24 hours')'''),
                    {'uid':row['user_id'],'cid':row['campaign_id']}).scalar()
                cancel=cancel or bool(recent)
            title,body=row['title'],row['body']
            data={'kind':'campaign','campaign_id':row['campaign_id'],'destination':row['destination']}
        else:
            from datetime import datetime,timezone
            cancel=cancel or not row['expires_at'] or row['expires_at']<=datetime.now(timezone.utc)
            title='BoraFut'
            locale=row['locale']
            body=('There is a new reply from the team.' if locale.startswith('en') else
                  'Hay una nueva respuesta del equipo.' if locale.startswith('es') else
                  'Há uma nova resposta da equipa.' if locale=='pt-PT' else 'Há uma nova resposta da equipe.')
            data={'kind':'support','thread_id':row['thread_id'] or ''}
        if cancel:
            db.execute(text("update public.push_outbox_v2 set state='cancelled',lease_until=null where id=cast(:id as uuid)"),{'id':identifier})
            return
        try:
            provider=self.sender.send(token=row['push_token'],title=title,body=body,data=data,delivery_id=identifier)
            db.execute(text("""update public.push_outbox_v2 set state='sent',sent_at=now(),provider_message_id=:provider,lease_until=null,last_error_code=null
                where id=cast(:id as uuid)"""),{'id':identifier,'provider':provider})
        except PushFailure as error:
            if error.deactivate:
                db.execute(text('update public.device_tokens_v2 set is_active=false where device_id=:id'),{'id':row['device_id']})
            retry=error.retryable and row['attempts']<5
            delay=min(3600,30*2**max(0,row['attempts']-1))
            db.execute(text('''update public.push_outbox_v2 set state=:state,last_error_code=:code,lease_until=null,
                available_at=now()+(:delay*interval '1 second') where id=cast(:id as uuid)'''),
                {'id':identifier,'state':'retry' if retry else 'failed','code':error.code,'delay':delay})
