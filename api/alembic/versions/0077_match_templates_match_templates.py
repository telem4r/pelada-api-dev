"""Timezone metadata and atomic idempotency for creating from a prior match."""
from alembic import op
revision = '0077_match_templates'
down_revision = '0076_monthly_exemptions'
branch_labels = None
depends_on = None
SQL = """
ALTER TABLE public.matches_v2 ADD COLUMN time_zone text;
CREATE TABLE public.match_clone_requests_v2 (
    user_id uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    group_id uuid NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
    request_key uuid NOT NULL,
    fingerprint text NOT NULL,
    match_id uuid REFERENCES public.matches_v2(id) ON DELETE SET NULL,
    source_match_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id, group_id, request_key)
);
ALTER TABLE public.match_clone_requests_v2 ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.match_clone_requests_v2 FROM PUBLIC;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        REVOKE ALL ON public.match_clone_requests_v2 FROM anon;
    END IF;
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
        REVOKE ALL ON public.match_clone_requests_v2 FROM authenticated;
    END IF;
END $$;
"""
def upgrade():
    op.execute(SQL)
def downgrade():
    op.execute('DROP TABLE public.match_clone_requests_v2')
    op.execute('ALTER TABLE public.matches_v2 DROP COLUMN time_zone')
