"""Private platform support, campaigns and persistent push delivery."""
from alembic import op
revision = '0078_communications'
down_revision = '0077_match_templates'
branch_labels = None
depends_on = None
SQL = """
CREATE TABLE public.platform_staff_v2 (
 user_id uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
 active boolean NOT NULL DEFAULT true, can_campaign boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.communication_preferences_v2 (
 user_id uuid PRIMARY KEY REFERENCES public.users(id) ON DELETE CASCADE,
 promotional_opt_in boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.device_tokens_v2 (
 device_id text PRIMARY KEY, user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 push_token text UNIQUE, platform text NOT NULL CHECK(platform in ('ios','android','web','other')),
 locale text NOT NULL DEFAULT 'pt-BR', is_active boolean NOT NULL DEFAULT true,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_device_tokens_user ON public.device_tokens_v2(user_id) WHERE is_active;
CREATE TABLE public.support_threads_v2 (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 request_key uuid NOT NULL, category text NOT NULL CHECK(category in ('question','problem','suggestion')),
 subject text, status text NOT NULL DEFAULT 'open' CHECK(status in ('open','in_progress','resolved')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,request_key)
);
CREATE TABLE public.support_messages_v2 (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 thread_id uuid NOT NULL REFERENCES public.support_threads_v2(id) ON DELETE CASCADE,
 author_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL, request_key uuid NOT NULL,
 author_kind text NOT NULL CHECK(author_kind in ('user','staff')), body text NOT NULL CHECK(length(trim(body)) between 1 and 4000),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT (now()+interval '720 hours'),
 UNIQUE(thread_id,author_user_id,request_key)
);
CREATE INDEX ix_support_message_expiry ON public.support_messages_v2(expires_at);
CREATE INDEX ix_support_message_thread ON public.support_messages_v2(thread_id,sequence_no);
CREATE INDEX ix_support_threads_user ON public.support_threads_v2(user_id,updated_at DESC);
CREATE TABLE public.platform_campaigns_v2 (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), created_by_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
 title text NOT NULL, body text NOT NULL, kind text NOT NULL CHECK(kind in ('promotional','announcement')),
 audience text NOT NULL CHECK(audience in ('all','group_managers')),
 locale text NOT NULL DEFAULT 'all' CHECK(locale in ('all','pt-BR','pt-PT','en-US','es-ES')),
 destination text NOT NULL DEFAULT 'communications' CHECK(destination in ('communications','home')),
 status text NOT NULL DEFAULT 'draft' CHECK(status in ('draft','scheduled','queued','sent','cancelled')),
 scheduled_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_platform_campaigns_due ON public.platform_campaigns_v2(status,scheduled_at);
CREATE TABLE public.campaign_recipients_v2 (
 campaign_id uuid NOT NULL REFERENCES public.platform_campaigns_v2(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 notification_id uuid NOT NULL DEFAULT gen_random_uuid(), read_at timestamptz,
 PRIMARY KEY(campaign_id,user_id)
);
CREATE TABLE public.push_outbox_v2 (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
 device_id text NOT NULL REFERENCES public.device_tokens_v2(device_id) ON DELETE CASCADE,
 message_id uuid REFERENCES public.support_messages_v2(id) ON DELETE CASCADE,
 campaign_id uuid REFERENCES public.platform_campaigns_v2(id) ON DELETE CASCADE,
 state text NOT NULL DEFAULT 'pending' CHECK(state in ('pending','processing','retry','sent','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(),
 lease_until timestamptz, provider_message_id text, last_error_code text, sent_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((message_id is null) <> (campaign_id is null)),
 UNIQUE(device_id,message_id), UNIQUE(device_id,campaign_id)
);
CREATE INDEX ix_push_outbox_due ON public.push_outbox_v2(state,available_at);
DO $$ DECLARE t text; r text; BEGIN
 FOREACH t IN ARRAY ARRAY['platform_staff_v2','communication_preferences_v2','device_tokens_v2',
 'support_threads_v2','support_messages_v2','platform_campaigns_v2','campaign_recipients_v2','push_outbox_v2'] LOOP
   EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
   EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC',t);
   FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
     IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
       EXECUTE format('REVOKE ALL ON public.%I FROM %I',t,r);
     END IF;
   END LOOP;
 END LOOP;
END $$;
"""
def upgrade():
    op.execute(SQL)
def downgrade():
    op.execute('DROP TABLE public.push_outbox_v2,public.campaign_recipients_v2,public.platform_campaigns_v2,public.support_messages_v2,public.support_threads_v2,public.device_tokens_v2,public.communication_preferences_v2,public.platform_staff_v2')
