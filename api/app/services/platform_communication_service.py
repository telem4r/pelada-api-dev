from __future__ import annotations

from datetime import datetime, timezone
import json

from fastapi import HTTPException
from sqlalchemy import text


class PlatformCommunicationService:
    def staff(self, db, user_id, *, campaigns=False):
        row = db.execute(text('''select can_campaign from public.platform_staff_v2
            where user_id=cast(:id as uuid) and active'''), {'id': user_id}).mappings().first()
        if not row or (campaigns and not row['can_campaign']):
            raise HTTPException(403, 'Acesso exclusivo da equipe autorizada da plataforma.')
        return dict(row)

    def thread(self, db, *, user_id, thread_id, staff=False, lock=False):
        if staff:
            self.staff(db, user_id)
        row = db.execute(text('''select id::text,user_id::text,status,category,
            case when created_at+interval '720 hours'>now() then subject else null end as subject,
            created_at,updated_at from public.support_threads_v2
            where id=cast(:id as uuid) and (:staff or user_id=cast(:uid as uuid))'''
            + (' for update' if lock else '')), {'id': str(thread_id), 'uid': user_id, 'staff': staff}).mappings().first()
        if not row:
            raise HTTPException(404, 'Atendimento não encontrado ou já expirado.')
        return dict(row)

    def threads(self, db, *, user_id, staff=False, limit=50, offset=0, status=None):
        if staff:
            self.staff(db, user_id)
        rows = db.execute(text('''select t.id::text,t.user_id::text,t.category,t.status,t.created_at,t.updated_at,
            case when t.created_at+interval '720 hours'>now() then t.subject else null end as subject,
            case when :staff then u.email else null end as user_email
            from public.support_threads_v2 t left join public.users u on u.id=t.user_id
            where (:staff or t.user_id=cast(:uid as uuid))
              and (cast(:status as text) is null or t.status=:status)
              and exists(select 1 from public.support_messages_v2 m where m.thread_id=t.id and m.expires_at>now())
            order by t.updated_at desc,t.id desc limit :limit offset :offset
        '''), {'uid': user_id, 'staff': staff, 'status': status, 'limit': limit, 'offset': offset}).mappings().all()
        return [dict(row) for row in rows]

    def messages(self, db, *, user_id, thread_id, staff=False, before=None):
        thread = self.thread(db, user_id=user_id, thread_id=thread_id, staff=staff)
        rows = db.execute(text('''select id::text,sequence_no,author_kind,body,created_at,expires_at
            from public.support_messages_v2 where thread_id=cast(:id as uuid) and expires_at>now()
              and (cast(:before as bigint) is null or sequence_no<:before)
            order by sequence_no desc limit 101
        '''), {'id': str(thread_id), 'before': before}).mappings().all()
        selected = rows[:100]
        return {'thread': thread, 'messages': [dict(row) for row in reversed(selected)], 'has_older': len(rows)>100,
                'retention_days': 30, 'server_now': datetime.now(timezone.utc)}

    def create_thread(self, db, *, user_id, payload):
        db.execute(text('select pg_advisory_xact_lock(hashtextextended(:scope,0))'), {'scope': 'support-create:'+user_id})
        existing = db.execute(text('''select id::text from public.support_threads_v2
            where user_id=cast(:uid as uuid) and request_key=cast(:key as uuid)'''),
            {'uid': user_id, 'key': str(payload.request_key)}).scalar()
        if existing:
            original = db.execute(text('''select body from public.support_messages_v2
                where thread_id=cast(:id as uuid) order by sequence_no limit 1'''), {'id': existing}).scalar()
            if original != payload.body:
                raise HTTPException(409, 'Essa confirmação já foi usada. Revise a mensagem.')
            return {'id': existing}
        count = db.execute(text('''select count(*) from public.support_threads_v2
            where user_id=cast(:uid as uuid) and created_at>now()-interval '1 hour' '''), {'uid': user_id}).scalar()
        if count >= 10:
            raise HTTPException(429, 'Limite de novos atendimentos atingido. Aguarde antes de tentar novamente.')
        identifier = db.execute(text('''insert into public.support_threads_v2(user_id,request_key,category,subject)
            values(cast(:uid as uuid),cast(:key as uuid),:category,:subject) returning id::text'''),
            {'uid': user_id, 'key': str(payload.request_key), 'category': payload.category, 'subject': payload.subject}).scalar_one()
        self.add_message(db, user_id=user_id, thread_id=identifier, payload=payload)
        return {'id': identifier}

    def add_message(self, db, *, user_id, thread_id, payload, staff=False):
        thread = self.thread(db, user_id=user_id, thread_id=thread_id, staff=staff, lock=True)
        existing = db.execute(text('''select id::text,body,expires_at from public.support_messages_v2
            where thread_id=cast(:id as uuid) and author_user_id=cast(:uid as uuid) and request_key=cast(:key as uuid)'''),
            {'id': str(thread_id), 'uid': user_id, 'key': str(payload.request_key)}).mappings().first()
        if existing:
            if existing['expires_at'] <= datetime.now(timezone.utc):
                raise HTTPException(410, 'A mensagem dessa confirmação já expirou.')
            if existing['body'] != payload.body:
                raise HTTPException(409, 'Essa confirmação já foi usada com outro conteúdo.')
            return {'id': existing['id']}
        db.execute(text('select pg_advisory_xact_lock(hashtextextended(:scope,0))'), {'scope': 'support-message:'+user_id})
        count = db.execute(text('''select count(*) from public.support_messages_v2
            where author_user_id=cast(:uid as uuid) and created_at>now()-interval '1 minute' '''), {'uid': user_id}).scalar()
        if count >= 30:
            raise HTTPException(429, 'Aguarde antes de enviar mais mensagens.')
        identifier = db.execute(text('''insert into public.support_messages_v2(thread_id,author_user_id,request_key,author_kind,body)
            values(cast(:id as uuid),cast(:uid as uuid),cast(:key as uuid),:kind,:body) returning id::text'''),
            {'id': str(thread_id), 'uid': user_id, 'key': str(payload.request_key), 'kind': 'staff' if staff else 'user', 'body': payload.body}).scalar_one()
        db.execute(text('''update public.support_threads_v2 set status=:status,updated_at=now()
            where id=cast(:id as uuid)'''), {'status': 'in_progress' if staff else 'open', 'id': str(thread_id)})
        if staff:
            # No private conversation text is copied into the notification or queue.
            db.execute(text('''insert into public.notification_events_v2(id,recipient_user_id,event_type,title,message,payload,is_read)
                values(cast(:id as uuid),cast(:uid as uuid),'support_reply','BoraFut','Há uma nova resposta da equipe.',
                cast(:payload as jsonb),false) on conflict(id) do nothing'''),
                {'id': identifier, 'uid': thread['user_id'], 'payload': json.dumps({'kind': 'support', 'thread_id': str(thread_id), 'message_id': identifier})})
            db.execute(text('''insert into public.push_outbox_v2(user_id,device_id,message_id)
                select cast(:uid as uuid),device_id,cast(:message as uuid) from public.device_tokens_v2
                where user_id=cast(:uid as uuid) and is_active and push_token is not null
                on conflict do nothing'''), {'uid': thread['user_id'], 'message': identifier})
        return {'id': identifier}

    def set_status(self, db, *, user_id, thread_id, status):
        self.thread(db, user_id=user_id, thread_id=thread_id, staff=True, lock=True)
        db.execute(text('update public.support_threads_v2 set status=:status,updated_at=now() where id=cast(:id as uuid)'),
                   {'status': status, 'id': str(thread_id)})
        return {'status': status}

    def preferences(self, db, user_id, enabled=None):
        if enabled is not None:
            db.execute(text('''insert into public.communication_preferences_v2(user_id,promotional_opt_in)
                values(cast(:id as uuid),:enabled) on conflict(user_id)
                do update set promotional_opt_in=excluded.promotional_opt_in,updated_at=now()'''), {'id': user_id, 'enabled': enabled})
        enabled = db.execute(text('select promotional_opt_in from public.communication_preferences_v2 where user_id=cast(:id as uuid)'), {'id': user_id}).scalar()
        return {'promotional_opt_in': bool(enabled)}

    def register_device(self, db, user_id, payload):
        # A device changing accounts belongs exclusively to its latest login.
        db.execute(text('select pg_advisory_xact_lock(hashtextextended(:scope,0))'), {'scope': 'push-token:'+payload.push_token})
        db.execute(text('''update public.device_tokens_v2 set is_active=false,push_token=null
            where push_token=:token and device_id<>:device'''), {'token': payload.push_token, 'device': payload.device_id})
        db.execute(text('''insert into public.device_tokens_v2(device_id,user_id,push_token,platform,locale,is_active)
            values(:device,cast(:uid as uuid),:token,:platform,:locale,:active)
            on conflict(device_id) do update set user_id=excluded.user_id,push_token=excluded.push_token,
              platform=excluded.platform,locale=excluded.locale,is_active=excluded.is_active,updated_at=now()'''),
            {'device': payload.device_id, 'uid': user_id, 'token': payload.push_token,
             'platform': payload.platform, 'locale': payload.locale, 'active': payload.is_active})
        return {'ok': True}

    def deactivate_device(self, db, user_id, device_id):
        db.execute(text('''update public.device_tokens_v2 set is_active=false,updated_at=now()
            where device_id=:device and user_id=cast(:uid as uuid)'''), {'device': device_id, 'uid': user_id})
        return {'ok': True}

    def campaigns(self, db, user_id, *, staff=False, limit=50, offset=0, campaign_id=None):
        if staff:
            self.staff(db, user_id, campaigns=True)
            rows = db.execute(text('''select c.*,
                (select count(*) from public.campaign_recipients_v2 r where r.campaign_id=c.id) as recipients,
                (select count(*) from public.push_outbox_v2 j where j.campaign_id=c.id and j.state='sent') as push_accepted,
                (select count(*) from public.push_outbox_v2 j where j.campaign_id=c.id and j.state='failed') as push_failed
                from public.platform_campaigns_v2 c order by c.created_at desc limit :limit offset :offset'''),
                {'limit': limit, 'offset': offset}).mappings().all()
        else:
            rows = db.execute(text('''select c.id::text,c.title,c.body,c.kind,c.destination,c.created_at,r.read_at
                from public.platform_campaigns_v2 c join public.campaign_recipients_v2 r on r.campaign_id=c.id
                left join public.communication_preferences_v2 p on p.user_id=r.user_id
                left join public.notification_settings_v2 s on s.user_id=r.user_id
                where r.user_id=cast(:uid as uuid) and c.status in ('queued','sent')
                  and (cast(:campaign_id as uuid) is null or c.id=cast(:campaign_id as uuid))
                  and coalesce(s.announcements_enabled,true)
                  and (c.kind<>'promotional' or coalesce(p.promotional_opt_in,false))
                order by c.scheduled_at desc,c.id desc limit :limit offset :offset'''),
                {'uid': user_id, 'limit': limit, 'offset': offset, 'campaign_id':str(campaign_id) if campaign_id else None}).mappings().all()
        return [dict(row) for row in rows]

    def create_campaign(self, db, user_id, payload):
        self.staff(db, user_id, campaigns=True)
        identifier = db.execute(text('''insert into public.platform_campaigns_v2
            (created_by_user_id,title,body,kind,audience,locale,destination,scheduled_at)
            values(cast(:uid as uuid),:title,:body,:kind,:audience,:locale,:destination,:scheduled_at) returning id::text'''),
            {**payload.model_dump(), 'uid': user_id}).scalar_one()
        return {'id': identifier, 'status': 'draft'}

    def publish_campaign(self, db, user_id, identifier, *, confirm):
        self.staff(db, user_id, campaigns=True)
        if confirm is not True:
            raise HTTPException(422, 'Revise e confirme o envio da campanha.')
        row = db.execute(text('select status,scheduled_at from public.platform_campaigns_v2 where id=cast(:id as uuid) for update'), {'id': str(identifier)}).mappings().first()
        if not row:
            raise HTTPException(404, 'Campanha não encontrada.')
        if row['status'] == 'scheduled':
            return {'status': 'scheduled'}
        if row['status'] != 'draft':
            raise HTTPException(409, 'A campanha já foi processada ou cancelada.')
        if row['scheduled_at'] and row['scheduled_at'] < datetime.now(timezone.utc):
            raise HTTPException(409, 'O agendamento do rascunho já passou. Atualize a data antes de confirmar.')
        db.execute(text("""update public.platform_campaigns_v2 set status='scheduled',scheduled_at=coalesce(scheduled_at,now()),updated_at=now()
            where id=cast(:id as uuid)"""), {'id': str(identifier)})
        return {'status': 'scheduled'}

    def update_campaign(self, db, user_id, identifier, payload):
        self.staff(db,user_id,campaigns=True)
        row=db.execute(text('''update public.platform_campaigns_v2 set title=:title,body=:body,kind=:kind,
            audience=:audience,locale=:locale,destination=:destination,scheduled_at=:scheduled_at,updated_at=now()
            where id=cast(:id as uuid) and status='draft' returning id::text'''),
            {**payload.model_dump(),'id':str(identifier)}).mappings().first()
        if not row:
            raise HTTPException(409,'Somente rascunhos podem ser editados.')
        return {'id':row['id'],'status':'draft'}

    def cancel_campaign(self, db, user_id, identifier):
        self.staff(db, user_id, campaigns=True)
        row = db.execute(text('''update public.platform_campaigns_v2 set status='cancelled',updated_at=now()
            where id=cast(:id as uuid) and status in ('draft','scheduled','queued') returning id'''), {'id': str(identifier)}).first()
        if not row:
            raise HTTPException(409, 'Campanha indisponível para cancelamento.')
        db.execute(text("update public.push_outbox_v2 set state='cancelled' where campaign_id=cast(:id as uuid) and state in ('pending','retry')"), {'id': str(identifier)})
        return {'status': 'cancelled'}

    def purge_expired(self, db, *, batch_size=1000):
        db.execute(text('''delete from public.notification_events_v2 n using public.support_messages_v2 m
            where n.id=m.id and n.event_type='support_reply' and m.expires_at<=clock_timestamp()'''))
        removed = db.execute(text('''delete from public.support_messages_v2 where id in (
            select id from public.support_messages_v2 where expires_at<=clock_timestamp()
            order by expires_at for update skip locked limit :batch)'''), {'batch': batch_size}).rowcount
        db.execute(text("update public.support_threads_v2 set subject=null where subject is not null and created_at+interval '720 hours'<=clock_timestamp()"))
        db.execute(text('''delete from public.support_threads_v2 where id in (
            select t.id from public.support_threads_v2 t where not exists(select 1 from public.support_messages_v2 m where m.thread_id=t.id)
            for update skip locked limit :batch)'''), {'batch': batch_size})
        lag = db.execute(text('''select coalesce(max(extract(epoch from clock_timestamp()-expires_at)),0)
            from public.support_messages_v2 where expires_at<=clock_timestamp()''')).scalar()
        return {'expired_messages_deleted': removed, 'expiry_lag_seconds': float(lag or 0)}
