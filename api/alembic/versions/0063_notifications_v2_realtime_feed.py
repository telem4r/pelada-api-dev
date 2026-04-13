"""notifications v2 realtime feed

Revision ID: 0063_notifications_v2_realtime_feed
Revises: 0062_finance_v2_obligations_entries_ledger
Create Date: 2026-03-22 00:00:00.000000
"""
from alembic import op

revision = '0063_notifications_v2_realtime_feed'
down_revision = '0062_finance_v2_obligations_entries_ledger'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.notification_events_v2 (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            recipient_user_id uuid NOT NULL,
            group_id uuid NULL,
            actor_user_id uuid NULL,
            event_type varchar(60) NOT NULL,
            title varchar(160) NOT NULL,
            message text NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            is_read boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            read_at timestamptz NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_events_v2_recipient ON public.notification_events_v2(recipient_user_id, created_at desc)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_events_v2_group ON public.notification_events_v2(group_id, created_at desc)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_events_v2_unread ON public.notification_events_v2(recipient_user_id, is_read, created_at desc)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_notification_events_v2_unread")
    op.execute("DROP INDEX IF EXISTS ix_notification_events_v2_group")
    op.execute("DROP INDEX IF EXISTS ix_notification_events_v2_recipient")
    op.execute("DROP TABLE IF EXISTS public.notification_events_v2")
