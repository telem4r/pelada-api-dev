"""add communication, social posts, ratings, notification settings tables

Revision ID: 0067_communication_social_tables
Revises: 0066_add_skill_rating_to_match_guests_v2
Create Date: 2026-03-25

Adds tables needed by the frontend antigo that were not part of the V2 migration:
- group_announcements_v2
- match_comments_v2
- group_activity_v2
- social_posts_v2
- social_post_likes_v2
- social_post_comments_v2
- player_ratings_v2
- notification_settings_v2
- user_push_tokens_v2
"""

from alembic import op
import sqlalchemy as sa

revision = "0067_communication_social_tables"
down_revision = "0066_add_skill_rating_to_match_guests_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ── Group Announcements ──────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.group_announcements_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            author_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            title       VARCHAR(255) NOT NULL,
            message     TEXT NOT NULL DEFAULT '',
            is_pinned   BOOLEAN NOT NULL DEFAULT false,
            published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_announcements_v2_group ON public.group_announcements_v2(group_id)")

    # ── Match Comments ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.match_comments_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            match_id    UUID NOT NULL REFERENCES public.matches_v2(id) ON DELETE CASCADE,
            author_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            message     TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_comments_v2_match ON public.match_comments_v2(match_id)")

    # ── Group Activity Log ───────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.group_activity_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            actor_user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            activity_type VARCHAR(80) NOT NULL,
            title       VARCHAR(255) NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_activity_v2_group ON public.group_activity_v2(group_id)")

    # ── Social Posts ─────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.social_posts_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            player_id   UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            post_type   VARCHAR(50) NOT NULL DEFAULT 'text',
            content     TEXT NOT NULL DEFAULT '',
            snapshot    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_social_posts_v2_player ON public.social_posts_v2(player_id)")

    # ── Social Post Likes ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.social_post_likes_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            post_id     UUID NOT NULL REFERENCES public.social_posts_v2(id) ON DELETE CASCADE,
            player_id   UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(post_id, player_id)
        )
    """)

    # ── Social Post Comments ─────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.social_post_comments_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            post_id     UUID NOT NULL REFERENCES public.social_posts_v2(id) ON DELETE CASCADE,
            player_id   UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            comment     TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Player Ratings ───────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.player_ratings_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            target_player_id UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            rater_player_id  UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            match_id    UUID REFERENCES public.matches_v2(id) ON DELETE SET NULL,
            group_id    UUID REFERENCES public.groups(id) ON DELETE SET NULL,
            skill       INT NOT NULL DEFAULT 3 CHECK (skill BETWEEN 1 AND 5),
            fair_play   INT NOT NULL DEFAULT 3 CHECK (fair_play BETWEEN 1 AND 5),
            commitment  INT NOT NULL DEFAULT 3 CHECK (commitment BETWEEN 1 AND 5),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_ratings_v2_target ON public.player_ratings_v2(target_player_id)")

    # ── Group Ratings ────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.group_ratings_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            rater_player_id UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            organization INT NOT NULL DEFAULT 3 CHECK (organization BETWEEN 1 AND 5),
            fair_play    INT NOT NULL DEFAULT 3 CHECK (fair_play BETWEEN 1 AND 5),
            level        INT NOT NULL DEFAULT 3 CHECK (level BETWEEN 1 AND 5),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Notification Settings ────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.notification_settings_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL UNIQUE REFERENCES public.users(id) ON DELETE CASCADE,
            matches_enabled BOOLEAN NOT NULL DEFAULT true,
            finance_enabled BOOLEAN NOT NULL DEFAULT true,
            announcements_enabled BOOLEAN NOT NULL DEFAULT true,
            comments_enabled BOOLEAN NOT NULL DEFAULT true,
            invites_enabled BOOLEAN NOT NULL DEFAULT true,
            fines_enabled BOOLEAN NOT NULL DEFAULT true,
            push_enabled BOOLEAN NOT NULL DEFAULT true,
            push_matches_enabled BOOLEAN NOT NULL DEFAULT true,
            push_finance_enabled BOOLEAN NOT NULL DEFAULT true,
            push_announcements_enabled BOOLEAN NOT NULL DEFAULT true,
            push_comments_enabled BOOLEAN NOT NULL DEFAULT true,
            push_invites_enabled BOOLEAN NOT NULL DEFAULT true,
            push_fines_enabled BOOLEAN NOT NULL DEFAULT true,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── User Push Tokens ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.user_push_tokens_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            token       TEXT NOT NULL,
            platform    VARCHAR(30) NOT NULL DEFAULT 'unknown',
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id, token)
        )
    """)

    # ── Updated at triggers ──────────────────────────────────────────
    for table in [
        'group_announcements_v2',
        'notification_settings_v2',
        'user_push_tokens_v2',
    ]:
        op.execute(f"""
            CREATE TRIGGER set_updated_at_{table}
            BEFORE UPDATE ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.trigger_set_updated_at()
        """)


def downgrade() -> None:
    for table in [
        'user_push_tokens_v2',
        'notification_settings_v2',
        'group_ratings_v2',
        'player_ratings_v2',
        'social_post_comments_v2',
        'social_post_likes_v2',
        'social_posts_v2',
        'group_activity_v2',
        'match_comments_v2',
        'group_announcements_v2',
    ]:
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")
