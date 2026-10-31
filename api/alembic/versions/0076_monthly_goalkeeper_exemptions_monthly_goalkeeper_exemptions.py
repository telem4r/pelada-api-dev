"""Individual, audited goalkeeper monthly exemptions, scoped to membership lifetime."""
from alembic import op

revision = '0076_monthly_exemptions'
down_revision = '0075_add_obligation_soft_delete'
branch_labels = None
depends_on = None

SQL = """
ALTER TABLE public.group_members ADD COLUMN monthly_exemption_epoch uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE public.group_members ADD COLUMN monthly_exemption_epoch_started_at timestamptz;
UPDATE public.group_members gm SET monthly_exemption_epoch_started_at = coalesce(
    nullif(to_jsonb(gm)->>'joined_at','')::timestamptz,
    nullif(to_jsonb(gm)->>'created_at','')::timestamptz, now());
ALTER TABLE public.group_members ALTER COLUMN monthly_exemption_epoch_started_at SET DEFAULT now();
ALTER TABLE public.group_members ALTER COLUMN monthly_exemption_epoch_started_at SET NOT NULL;
CREATE TABLE public.monthly_exemption_events_v2 (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    membership_id uuid NOT NULL REFERENCES public.group_members(id) ON DELETE CASCADE,
    membership_epoch uuid NOT NULL,
    group_id uuid NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
    enabled boolean NOT NULL,
    reason text NOT NULL DEFAULT 'goalkeeper' CHECK(reason = 'goalkeeper'),
    effective_month date NOT NULL CHECK(extract(day from effective_month) = 1),
    actor_user_id uuid REFERENCES public.users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_monthly_exemptions_scope ON public.monthly_exemption_events_v2(membership_id, membership_epoch, effective_month DESC, sequence_no DESC);
ALTER TABLE public.monthly_exemption_events_v2 ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.monthly_exemption_events_v2 FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        REVOKE ALL ON public.monthly_exemption_events_v2 FROM anon;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
        REVOKE ALL ON public.monthly_exemption_events_v2 FROM authenticated;
    END IF;
END $$;
CREATE FUNCTION public.borafut_reset_monthly_exemption_epoch() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog, public AS $$
BEGIN
    IF OLD.status::text IS DISTINCT FROM NEW.status::text AND NEW.status::text = 'active' THEN
        NEW.monthly_exemption_epoch := gen_random_uuid();
        NEW.monthly_exemption_epoch_started_at := now();
    ELSE
        NEW.monthly_exemption_epoch := OLD.monthly_exemption_epoch;
        NEW.monthly_exemption_epoch_started_at := OLD.monthly_exemption_epoch_started_at;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.borafut_reset_monthly_exemption_epoch() FROM PUBLIC;
CREATE TRIGGER borafut_monthly_exemption_membership_lifetime
BEFORE UPDATE ON public.group_members FOR EACH ROW
EXECUTE FUNCTION public.borafut_reset_monthly_exemption_epoch();
"""


def upgrade():
    op.execute(SQL)


def downgrade():
    # An explicit operator downgrade removes this feature's audit history.
    op.execute('DROP TRIGGER borafut_monthly_exemption_membership_lifetime ON public.group_members')
    op.execute('DROP FUNCTION public.borafut_reset_monthly_exemption_epoch()')
    op.execute('DROP TABLE public.monthly_exemption_events_v2')
    op.execute('ALTER TABLE public.group_members DROP COLUMN monthly_exemption_epoch, DROP COLUMN monthly_exemption_epoch_started_at')
