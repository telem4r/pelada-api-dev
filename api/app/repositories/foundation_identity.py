from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("app.foundation_identity")


@dataclass(frozen=True)
class _PlayerRow:
    id: str
    user_id: str
    display_name: str | None
    full_name: str | None
    nickname: str | None
    primary_position: str | None
    secondary_position: str | None
    avatar_url: str | None
    preferred_foot: str | None
    rating: int | None
    is_public: bool | None
    is_active: bool | None


class FoundationIdentityRepository:
    """Repository para bootstrap e fetch de identidade do utilizador.

    Schema UUID-native: users.id=UUID, players.user_id=UUID, players.display_name.
    Implementa account-linking por email com merge seguro e idempotente.
    """

    def bootstrap_user_and_player(
        self,
        db: Session,
        *,
        user_id: str,
        email: str | None,
        display_name: str,
        full_name: str | None,
        nickname: str | None,
    ) -> dict[str, Any]:
        link_action = 'existing_user'
        linked_from_user_id: str | None = None
        normalized_email = email.strip().lower() if isinstance(email, str) and email.strip() else None

        existing_by_email = self._get_user_by_email(db, normalized_email)
        if existing_by_email and existing_by_email['id'] != user_id:
            linked_from_user_id = existing_by_email['id']
            self._ensure_user(db, user_id=user_id, email=normalized_email)
            self._merge_user_identity(
                db,
                source_user_id=existing_by_email['id'],
                target_user_id=user_id,
                email=normalized_email,
            )
            link_action = 'linked_existing_email'
        else:
            self._ensure_user(db, user_id=user_id, email=normalized_email)

        self._upsert_player(
            db,
            user_id=user_id,
            display_name=display_name,
            full_name=full_name,
            nickname=nickname,
        )

        return {
            'link_action': link_action,
            'linked_from_user_id': linked_from_user_id,
            'created_user': not bool(self._get_user_by_id(db, user_id)),
        }

    def fetch_session_identity(
        self, db: Session, *, user_id: str, email: str | None = None
    ) -> dict[str, Any] | None:
        row = db.execute(
            text("""
                SELECT
                    u.id::text          AS user_id,
                    u.email             AS user_email,
                    p.id::text          AS player_id,
                    p.user_id::text     AS player_user_id,
                    p.display_name,
                    p.full_name,
                    p.nickname,
                    p.primary_position,
                    p.secondary_position,
                    p.avatar_url,
                    p.is_public,
                    p.is_active
                FROM public.users u
                JOIN public.players p ON p.user_id = u.id
                WHERE u.id = cast(:user_id AS uuid)
                LIMIT 1
            """),
            {"user_id": user_id},
        ).mappings().first()
        return dict(row) if row else None

    def _get_user_by_id(self, db: Session, user_id: str) -> dict[str, Any] | None:
        row = db.execute(
            text("""
                SELECT id::text AS id, email
                FROM public.users
                WHERE id = cast(:user_id AS uuid)
                LIMIT 1
            """),
            {'user_id': user_id},
        ).mappings().first()
        return dict(row) if row else None

    def _get_user_by_email(self, db: Session, email: str | None) -> dict[str, Any] | None:
        normalized_email = email.strip().lower() if isinstance(email, str) and email.strip() else None
        if not normalized_email:
            return None
        row = db.execute(
            text("""
                SELECT id::text AS id, email
                FROM public.users
                WHERE lower(email) = lower(:email)
                LIMIT 1
            """),
            {'email': normalized_email},
        ).mappings().first()
        return dict(row) if row else None

    def _ensure_user(self, db: Session, *, user_id: str, email: str | None) -> None:
        normalized_email = email.strip().lower() if isinstance(email, str) and email.strip() else None
        db.execute(
            text("""
                INSERT INTO public.users (id, email, created_at, updated_at)
                VALUES (cast(:user_id AS uuid), :email, now(), now())
                ON CONFLICT (id) DO UPDATE
                SET email = coalesce(excluded.email, public.users.email),
                    updated_at = now()
            """),
            {'user_id': user_id, 'email': normalized_email},
        )

    def _upsert_player(
        self,
        db: Session,
        *,
        user_id: str,
        display_name: str,
        full_name: str | None,
        nickname: str | None,
    ) -> None:
        db.execute(
            text("""
                INSERT INTO public.players (
                    id, user_id, display_name, full_name, nickname,
                    is_public, is_active, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), cast(:user_id AS uuid), :display_name,
                    :full_name, :nickname, true, true, now(), now()
                )
                ON CONFLICT (user_id) DO UPDATE
                SET display_name = coalesce(nullif(public.players.display_name, ''), excluded.display_name),
                    full_name    = coalesce(public.players.full_name, excluded.full_name),
                    nickname     = coalesce(public.players.nickname, excluded.nickname),
                    updated_at   = now()
            """),
            {
                'user_id': user_id,
                'display_name': display_name,
                'full_name': full_name,
                'nickname': nickname,
            },
        )

    def _get_player_by_user_id(self, db: Session, user_id: str) -> _PlayerRow | None:
        row = db.execute(
            text("""
                SELECT
                    id::text AS id,
                    user_id::text AS user_id,
                    display_name,
                    full_name,
                    nickname,
                    primary_position,
                    secondary_position,
                    avatar_url,
                    preferred_foot,
                    rating,
                    is_public,
                    is_active
                FROM public.players
                WHERE user_id = cast(:user_id AS uuid)
                LIMIT 1
            """),
            {'user_id': user_id},
        ).mappings().first()
        return _PlayerRow(**dict(row)) if row else None

    def _merge_user_identity(self, db: Session, *, source_user_id: str, target_user_id: str, email: str | None) -> None:
        normalized_email = email.strip().lower() if isinstance(email, str) and email.strip() else None
        if source_user_id == target_user_id:
            return

        source_player = self._get_player_by_user_id(db, source_user_id)
        target_player = self._get_player_by_user_id(db, target_user_id)

        if source_player and target_player:
            self._merge_player_rows(db, source_player=source_player, target_player=target_player)
        elif source_player and not target_player:
            db.execute(
                text("""
                    UPDATE public.players
                    SET user_id = cast(:target_user_id AS uuid),
                        updated_at = now()
                    WHERE id = cast(:player_id AS uuid)
                """),
                {'target_user_id': target_user_id, 'player_id': source_player.id},
            )

        self._migrate_fk_references(
            db,
            referenced_table='users',
            source_id=source_user_id,
            target_id=target_user_id,
            skip_pairs={('public', 'players', 'user_id')},
        )

        db.execute(
            text("""
                DELETE FROM public.users
                WHERE id = cast(:source_user_id AS uuid)
            """),
            {'source_user_id': source_user_id},
        )

        db.execute(
            text("""
                UPDATE public.users
                SET email = coalesce(:email, email),
                    updated_at = now()
                WHERE id = cast(:target_user_id AS uuid)
            """),
            {'target_user_id': target_user_id, 'email': normalized_email},
        )

    def _merge_player_rows(self, db: Session, *, source_player: _PlayerRow, target_player: _PlayerRow) -> None:
        if source_player.id == target_player.id:
            return

        db.execute(
            text("""
                UPDATE public.players
                SET
                    display_name = coalesce(nullif(display_name, ''), :source_display_name),
                    full_name = coalesce(full_name, :source_full_name),
                    nickname = coalesce(nickname, :source_nickname),
                    primary_position = coalesce(primary_position, :source_primary_position),
                    secondary_position = coalesce(secondary_position, :source_secondary_position),
                    avatar_url = coalesce(avatar_url, :source_avatar_url),
                    preferred_foot = coalesce(preferred_foot, :source_preferred_foot),
                    rating = GREATEST(coalesce(rating, 0), coalesce(:source_rating, 0)),
                    is_public = coalesce(is_public, :source_is_public),
                    is_active = coalesce(is_active, :source_is_active),
                    updated_at = now()
                WHERE id = cast(:target_player_id AS uuid)
            """),
            {
                'target_player_id': target_player.id,
                'source_display_name': source_player.display_name,
                'source_full_name': source_player.full_name,
                'source_nickname': source_player.nickname,
                'source_primary_position': source_player.primary_position,
                'source_secondary_position': source_player.secondary_position,
                'source_avatar_url': source_player.avatar_url,
                'source_preferred_foot': source_player.preferred_foot,
                'source_rating': source_player.rating,
                'source_is_public': source_player.is_public,
                'source_is_active': source_player.is_active,
            },
        )

        self._migrate_fk_references(
            db,
            referenced_table='players',
            source_id=source_player.id,
            target_id=target_player.id,
        )

        db.execute(
            text("""
                DELETE FROM public.players
                WHERE id = cast(:source_player_id AS uuid)
            """),
            {'source_player_id': source_player.id},
        )

    def _migrate_fk_references(
        self,
        db: Session,
        *,
        referenced_table: str,
        source_id: str,
        target_id: str,
        skip_pairs: set[tuple[str, str, str]] | None = None,
    ) -> None:
        if source_id == target_id:
            return

        skip_pairs = skip_pairs or set()
        for schema_name, table_name, column_name in self._list_fk_references(db, referenced_table=referenced_table):
            if (schema_name, table_name, column_name) in skip_pairs:
                continue
            qualified_table = f'{self._quote_ident(schema_name)}.{self._quote_ident(table_name)}'
            quoted_column = self._quote_ident(column_name)
            db.execute(
                text(f"""
                    UPDATE {qualified_table}
                    SET {quoted_column} = cast(:target_id AS uuid)
                    WHERE {quoted_column} = cast(:source_id AS uuid)
                """),
                {'source_id': source_id, 'target_id': target_id},
            )

    def _list_fk_references(self, db: Session, *, referenced_table: str) -> list[tuple[str, str, str]]:
        rows = db.execute(
            text("""
                SELECT
                    ns.nspname AS schema_name,
                    cl.relname AS table_name,
                    att.attname AS column_name
                FROM pg_constraint con
                JOIN pg_class cl ON cl.oid = con.conrelid
                JOIN pg_namespace ns ON ns.oid = cl.relnamespace
                JOIN unnest(con.conkey) WITH ORDINALITY AS cols(attnum, ord) ON true
                JOIN pg_attribute att ON att.attrelid = cl.oid AND att.attnum = cols.attnum
                WHERE con.contype = 'f'
                  AND con.confrelid = :qualified_table::regclass
                  AND ns.nspname = 'public'
            """),
            {'qualified_table': f'public.{referenced_table}'},
        ).all()
        return [(str(schema), str(table), str(column)) for schema, table, column in rows]

    @staticmethod
    def _quote_ident(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'
