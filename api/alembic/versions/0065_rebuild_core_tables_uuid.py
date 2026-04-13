"""rebuild core tables with uuid schema

Revision ID: 0065_rebuild_core_tables_uuid
Revises: 0064_social_v2_follow_feed
Create Date: 2026-03-22

MOTIVAÇÃO:
    Os repositórios V2 (groups_v2, matches_v2, finance_v2, social_v2, foundation)
    foram escritos para um schema UUID-native que NUNCA foi criado por nenhuma migration.
    As tabelas core (users, players, groups, group_members, group_join_requests) ainda
    usavam INTEGER IDs e colunas com nomes diferentes do que o código V2 espera.

    Como o banco é NOVO e ZERADO (sem dados reais), esta migration:
    1. Dropa TODAS as tabelas (V2 e legacy) com CASCADE
    2. Cria os enums necessários (group_type_enum, group_role_enum, etc.)
    3. Recria as tabelas core com UUID IDs e colunas corretas
    4. Recria as tabelas V2 com FKs UUID
    5. Recria as tabelas de comunicação/social/achievements

    Resultado: schema 100% consistente com os repositórios V2.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0065_rebuild_core_tables_uuid"
down_revision = "0064_social_v2_follow_feed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ================================================================
    # STEP 0: Extensions
    # ================================================================
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")

    # ================================================================
    # STEP 1: DROP everything (order matters due to FKs)
    # ================================================================
    op.execute("""
        DO $$ DECLARE r RECORD;
        BEGIN
            FOR r IN (
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename <> 'alembic_version'
            ) LOOP
                EXECUTE 'DROP TABLE IF EXISTS public.' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
        END $$;
    """)

    # ================================================================
    # STEP 2: DROP old enums, then create all needed enums
    # ================================================================
    op.execute("""
        DO $$ DECLARE r RECORD;
        BEGIN
            FOR r IN (
                SELECT typname FROM pg_type
                WHERE typtype = 'e'
                  AND typnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
            ) LOOP
                EXECUTE 'DROP TYPE IF EXISTS public.' || quote_ident(r.typname) || ' CASCADE';
            END LOOP;
        END $$;
    """)

    # Core enums
    op.execute("""
        CREATE TYPE group_type_enum AS ENUM ('avulso', 'hibrido');
        CREATE TYPE group_role_enum AS ENUM ('owner', 'admin', 'member');
        CREATE TYPE membership_status_enum AS ENUM ('pending', 'active', 'rejected', 'removed');
        CREATE TYPE billing_type_enum AS ENUM ('mensalista', 'avulso');
    """)

    # Match V2 enums
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_status_enum_v2') THEN
                CREATE TYPE match_status_enum_v2 AS ENUM ('scheduled', 'in_progress', 'finished', 'cancelled');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_presence_status_enum_v2') THEN
                CREATE TYPE match_presence_status_enum_v2 AS ENUM ('confirmado', 'espera');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_position_enum_v2') THEN
                CREATE TYPE match_position_enum_v2 AS ENUM ('linha', 'goleiro');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'match_draw_status_enum_v2') THEN
                CREATE TYPE match_draw_status_enum_v2 AS ENUM ('pending', 'generated');
            END IF;
        END$$;
    """)

    # Finance V2 enums
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'obligation_type_enum_v2') THEN
                CREATE TYPE obligation_type_enum_v2 AS ENUM ('monthly', 'match', 'fine', 'other');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'obligation_status_enum_v2') THEN
                CREATE TYPE obligation_status_enum_v2 AS ENUM ('pending', 'paid', 'cancelled', 'waived');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'entry_type_enum_v2') THEN
                CREATE TYPE entry_type_enum_v2 AS ENUM ('income', 'expense');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'ledger_direction_enum_v2') THEN
                CREATE TYPE ledger_direction_enum_v2 AS ENUM ('credit', 'debit');
            END IF;
        END$$;
    """)

    # ================================================================
    # STEP 3: Core tables - UUID native
    # ================================================================

    # --- users ---
    op.execute("""
        CREATE TABLE public.users (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email       VARCHAR(255) UNIQUE,
            name        VARCHAR(120),
            password_hash VARCHAR(255),
            avatar_url  VARCHAR(500),
            first_name  VARCHAR(100),
            last_name   VARCHAR(100),
            birth_date  DATE,
            favorite_team VARCHAR(120),
            birth_country VARCHAR(100),
            birth_state VARCHAR(100),
            birth_city  VARCHAR(120),
            current_country VARCHAR(100),
            current_state VARCHAR(100),
            current_city VARCHAR(120),
            position    VARCHAR(80),
            preferred_foot VARCHAR(20),
            language    VARCHAR(10),
            refresh_token VARCHAR(255),
            refresh_token_hash VARCHAR(255),
            refresh_token_expires_at TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_users_email ON public.users(email)")

    # --- teams ---
    op.execute("""
        CREATE TABLE public.teams (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_id    UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            name        VARCHAR(120) NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- players ---
    op.execute("""
        CREATE TABLE public.players (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            display_name VARCHAR(120) NOT NULL,
            full_name   VARCHAR(200),
            nickname    VARCHAR(80),
            team_id     UUID REFERENCES public.teams(id) ON DELETE SET NULL,
            primary_position VARCHAR(80),
            secondary_position VARCHAR(80),
            preferred_foot VARCHAR(20),
            avatar_url  VARCHAR(500),
            bio         TEXT,
            city        VARCHAR(120),
            skill_level VARCHAR(30),
            rating      INTEGER NOT NULL DEFAULT 0,
            is_public   BOOLEAN NOT NULL DEFAULT true,
            is_active   BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(user_id)
        )
    """)
    op.execute("CREATE INDEX ix_players_user_id ON public.players(user_id)")

    # --- groups ---
    op.execute("""
        CREATE TABLE public.groups (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            name        VARCHAR(120) NOT NULL,
            description TEXT,
            currency    VARCHAR(10) NOT NULL DEFAULT 'BRL',
            avatar_url  VARCHAR(500),
            country     VARCHAR(100),
            state       VARCHAR(100),
            city        VARCHAR(120),
            modality    VARCHAR(50),
            group_type  group_type_enum NOT NULL DEFAULT 'avulso',
            gender_type VARCHAR(50),
            payment_method VARCHAR(50),
            payment_key VARCHAR(255),
            venue_cost  FLOAT,
            per_person_cost FLOAT,
            monthly_cost FLOAT,
            single_cost FLOAT,
            single_waitlist_release_days INTEGER NOT NULL DEFAULT 0,
            payment_due_day INTEGER,
            fine_enabled BOOLEAN NOT NULL DEFAULT false,
            fine_amount FLOAT,
            fine_reason VARCHAR(255),
            is_public   BOOLEAN NOT NULL DEFAULT false,
            is_active   BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_groups_owner_user_id ON public.groups(owner_user_id)")

    # --- group_members ---
    op.execute("""
        CREATE TABLE public.group_members (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            player_id   UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            role        group_role_enum NOT NULL DEFAULT 'member',
            status      membership_status_enum NOT NULL DEFAULT 'pending',
            billing_type billing_type_enum NOT NULL DEFAULT 'avulso',
            skill_rating INTEGER NOT NULL DEFAULT 3,
            joined_at   TIMESTAMPTZ DEFAULT now(),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(group_id, user_id),
            UNIQUE(group_id, player_id)
        )
    """)
    op.execute("CREATE INDEX ix_group_members_group_id ON public.group_members(group_id)")
    op.execute("CREATE INDEX ix_group_members_user_id ON public.group_members(user_id)")
    op.execute("CREATE INDEX ix_group_members_player_id ON public.group_members(player_id)")

    # --- group_join_requests ---
    op.execute("""
        CREATE TABLE public.group_join_requests (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            player_id   UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            status      membership_status_enum NOT NULL DEFAULT 'pending',
            message     TEXT,
            reviewed_by_user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            reviewed_at TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(group_id, player_id)
        )
    """)
    op.execute("CREATE INDEX ix_group_join_requests_group_id ON public.group_join_requests(group_id)")

    # --- group_invitations ---
    op.execute("""
        CREATE TABLE public.group_invitations (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            invited_email VARCHAR(255) NOT NULL,
            invited_by_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            status      membership_status_enum NOT NULL DEFAULT 'pending',
            token       VARCHAR(255) NOT NULL,
            expires_at  TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_group_invitations_group_id ON public.group_invitations(group_id)")
    op.execute("CREATE INDEX ix_group_invitations_token ON public.group_invitations(token)")

    # ================================================================
    # STEP 4: Matches V2
    # ================================================================
    op.execute("""
        CREATE TABLE public.matches_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            created_by_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
            title       VARCHAR(120),
            status      match_status_enum_v2 NOT NULL DEFAULT 'scheduled',
            starts_at   TIMESTAMPTZ NOT NULL,
            ends_at     TIMESTAMPTZ NOT NULL,
            location_name VARCHAR(160),
            notes       TEXT,
            line_slots  INTEGER NOT NULL DEFAULT 0,
            goalkeeper_slots INTEGER NOT NULL DEFAULT 0,
            draw_status match_draw_status_enum_v2 NOT NULL DEFAULT 'pending',
            value_per_player FLOAT NOT NULL DEFAULT 0.0,
            price_cents INTEGER,
            currency    VARCHAR(10),
            started_at  TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            roster_locked BOOLEAN NOT NULL DEFAULT false,
            draw_locked BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_matches_v2_slots_positive CHECK ((line_slots + goalkeeper_slots) > 0),
            CONSTRAINT ck_matches_v2_ends_after_start CHECK (ends_at > starts_at)
        )
    """)
    op.execute("CREATE INDEX ix_matches_v2_group_id_starts_at ON public.matches_v2(group_id, starts_at DESC)")

    # --- match_participants_v2 ---
    op.execute("""
        CREATE TABLE public.match_participants_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            match_id    UUID NOT NULL REFERENCES public.matches_v2(id) ON DELETE CASCADE,
            player_id   UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            position    match_position_enum_v2 NOT NULL,
            status      match_presence_status_enum_v2 NOT NULL,
            queue_order INTEGER NOT NULL DEFAULT 1,
            has_arrived BOOLEAN NOT NULL DEFAULT false,
            is_paid     BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(match_id, player_id)
        )
    """)
    op.execute("CREATE INDEX ix_match_participants_v2_match_status ON public.match_participants_v2(match_id, status, position, queue_order)")

    # --- match_guests_v2 ---
    op.execute("""
        CREATE TABLE public.match_guests_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            match_id    UUID NOT NULL REFERENCES public.matches_v2(id) ON DELETE CASCADE,
            created_by_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
            name        VARCHAR(120) NOT NULL,
            position    match_position_enum_v2 NOT NULL,
            status      match_presence_status_enum_v2 NOT NULL,
            queue_order INTEGER NOT NULL DEFAULT 1,
            has_arrived BOOLEAN NOT NULL DEFAULT false,
            is_paid     BOOLEAN NOT NULL DEFAULT false,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_match_guests_v2_match_status ON public.match_guests_v2(match_id, status, position, queue_order)")

    # --- match_draws_v2 ---
    op.execute("""
        CREATE TABLE public.match_draws_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            match_id    UUID NOT NULL REFERENCES public.matches_v2(id) ON DELETE CASCADE,
            generated_by_user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            team_count  INTEGER NOT NULL DEFAULT 2,
            generated_at TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(match_id)
        )
    """)

    # --- match_draw_entries_v2 ---
    op.execute("""
        CREATE TABLE public.match_draw_entries_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            draw_id     UUID NOT NULL REFERENCES public.match_draws_v2(id) ON DELETE CASCADE,
            team_number INTEGER NOT NULL DEFAULT 0,
            entry_kind  VARCHAR(20),
            participant_id UUID REFERENCES public.match_participants_v2(id) ON DELETE SET NULL,
            guest_id    UUID REFERENCES public.match_guests_v2(id) ON DELETE SET NULL,
            player_id   UUID REFERENCES public.players(id) ON DELETE SET NULL,
            display_name VARCHAR(120),
            position    VARCHAR(30),
            skill_rating INTEGER,
            entry_order INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- match_events_v2 ---
    op.execute("""
        CREATE TABLE public.match_events_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            match_id    UUID NOT NULL REFERENCES public.matches_v2(id) ON DELETE CASCADE,
            event_type  VARCHAR(40) NOT NULL,
            event_data  JSONB NOT NULL DEFAULT '{}',
            minute      INTEGER,
            half        INTEGER,
            participant_id UUID REFERENCES public.match_participants_v2(id) ON DELETE SET NULL,
            guest_id    UUID REFERENCES public.match_guests_v2(id) ON DELETE SET NULL,
            player_id   UUID REFERENCES public.players(id) ON DELETE SET NULL,
            display_name VARCHAR(120),
            position    VARCHAR(30),
            notes       TEXT,
            created_by_user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            team_number INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- match_player_stats_v2 ---
    op.execute("""
        CREATE TABLE public.match_player_stats_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            match_id    UUID NOT NULL REFERENCES public.matches_v2(id) ON DELETE CASCADE,
            player_id   UUID REFERENCES public.players(id) ON DELETE CASCADE,
            team_number INTEGER NOT NULL DEFAULT 0,
            entry_kind  VARCHAR(20),
            participant_id UUID REFERENCES public.match_participants_v2(id) ON DELETE SET NULL,
            guest_id    UUID REFERENCES public.match_guests_v2(id) ON DELETE SET NULL,
            display_name VARCHAR(120),
            position    VARCHAR(30),
            goals       INTEGER NOT NULL DEFAULT 0,
            assists     INTEGER NOT NULL DEFAULT 0,
            own_goals   INTEGER NOT NULL DEFAULT 0,
            yellow_cards INTEGER NOT NULL DEFAULT 0,
            red_cards   INTEGER NOT NULL DEFAULT 0,
            saves       INTEGER NOT NULL DEFAULT 0,
            mvp         BOOLEAN NOT NULL DEFAULT false,
            rating      FLOAT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ================================================================
    # STEP 5: Finance V2
    # ================================================================
    op.execute("""
        CREATE TABLE public.finance_obligations_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            user_id     UUID REFERENCES public.users(id) ON DELETE SET NULL,
            player_id   UUID REFERENCES public.players(id) ON DELETE SET NULL,
            match_id    UUID REFERENCES public.matches_v2(id) ON DELETE SET NULL,
            source_type VARCHAR(40) NOT NULL DEFAULT 'other',
            title       VARCHAR(255),
            description TEXT,
            amount      FLOAT NOT NULL DEFAULT 0,
            currency    VARCHAR(10) NOT NULL DEFAULT 'BRL',
            status      VARCHAR(30) NOT NULL DEFAULT 'aberta',
            due_date    DATE,
            competence_month INTEGER,
            competence_year INTEGER,
            created_by_user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE public.finance_entries_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            obligation_id UUID REFERENCES public.finance_obligations_v2(id) ON DELETE SET NULL,
            user_id     UUID REFERENCES public.users(id) ON DELETE SET NULL,
            player_id   UUID REFERENCES public.players(id) ON DELETE SET NULL,
            match_id    UUID REFERENCES public.matches_v2(id) ON DELETE SET NULL,
            entry_type  VARCHAR(20) NOT NULL,
            category    VARCHAR(40),
            amount      FLOAT NOT NULL DEFAULT 0,
            currency    VARCHAR(10) NOT NULL DEFAULT 'BRL',
            paid_at     TIMESTAMPTZ,
            notes       TEXT,
            created_by_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE RESTRICT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE public.finance_ledger_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id    UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
            obligation_id UUID REFERENCES public.finance_obligations_v2(id) ON DELETE SET NULL,
            entry_id    UUID REFERENCES public.finance_entries_v2(id) ON DELETE SET NULL,
            movement_type VARCHAR(40) NOT NULL,
            direction   VARCHAR(10) NOT NULL,
            amount      FLOAT NOT NULL DEFAULT 0,
            balance_impact FLOAT NOT NULL DEFAULT 0,
            description TEXT,
            reference_date TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ================================================================
    # STEP 6: Notifications V2
    # ================================================================
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.notification_events_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            recipient_user_id UUID NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
            group_id    UUID REFERENCES public.groups(id) ON DELETE CASCADE,
            actor_user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            event_type  VARCHAR(60) NOT NULL,
            title       VARCHAR(255),
            message     TEXT,
            payload     JSONB NOT NULL DEFAULT '{}',
            is_read     BOOLEAN NOT NULL DEFAULT false,
            read_at     TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_notification_events_v2_user ON public.notification_events_v2(recipient_user_id, is_read, created_at DESC)")

    # ================================================================
    # STEP 7: Social V2
    # ================================================================
    op.execute("""
        CREATE TABLE public.social_follows_v2 (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            follower_player_id UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            followed_player_id UUID NOT NULL REFERENCES public.players(id) ON DELETE CASCADE,
            followed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(follower_player_id, followed_player_id)
        )
    """)

    # ================================================================
    # STEP 8: updated_at trigger
    # ================================================================
    op.execute("""
        CREATE OR REPLACE FUNCTION public.trigger_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    for table in [
        'users', 'teams', 'players', 'groups', 'group_members',
        'group_join_requests', 'matches_v2', 'match_participants_v2',
        'match_guests_v2', 'match_player_stats_v2',
        'finance_obligations_v2', 'finance_entries_v2',
    ]:
        op.execute(f"""
            DROP TRIGGER IF EXISTS set_updated_at ON public.{table};
            CREATE TRIGGER set_updated_at
                BEFORE UPDATE ON public.{table}
                FOR EACH ROW
                EXECUTE FUNCTION public.trigger_set_updated_at();
        """)


def downgrade() -> None:
    # This migration is destructive by design (fresh DB).
    # Downgrade would need to recreate the entire legacy schema.
    # Not implemented since we're moving forward.
    raise NotImplementedError(
        "Downgrade from UUID schema to legacy INTEGER schema is not supported. "
        "Create a fresh database with the legacy migrations if needed."
    )
