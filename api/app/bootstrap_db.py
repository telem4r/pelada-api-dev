import os
import zlib
import logging
import psycopg2

logger = logging.getLogger("app.bootstrap_db")


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _lock_id(key: str) -> int:
    return zlib.crc32(key.encode("utf-8"))


def ensure_groups_table_exists(lock_key: str = "borafut-bootstrap-groups") -> None:
    """
    ✅ Resolução definitiva do erro:
    'relation "groups" does not exist'

    Cria a tabela 'groups' e índices mínimos caso não existam, com advisory lock,
    antes de rodar o Alembic.
    """
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        logger.warning("bootstrap: DATABASE_URL not set; skipping bootstrap.")
        return

    conn = psycopg2.connect(_normalize_db_url(db_url))
    conn.autocommit = True
    lock = _lock_id(lock_key)

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s);", (lock,))
            got = bool(cur.fetchone()[0])
            if not got:
                logger.info("bootstrap: another instance is bootstrapping; skipping.")
                return

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = 'groups'
                );
                """
            )
            exists = bool(cur.fetchone()[0])

        if exists:
            logger.info("bootstrap: table groups already exists.")
            return

        logger.warning("bootstrap: table groups DOES NOT exist. Creating minimal schema...")

        # Tabela mínima compatível com migrations posteriores (0004 cria muitos campos,
        # mas para destravar basta existir com PK e owner_id).
        # Depois o Alembic adiciona o resto.
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS ix_groups_id ON groups (id);")
            cur.execute("CREATE INDEX IF NOT EXISTS ix_groups_owner_id ON groups (owner_id);")

        logger.warning("bootstrap: minimal groups table created successfully.")
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s);", (lock,))
        except Exception:
            logger.exception("bootstrap: failed to release advisory lock (ignored)")
        conn.close()
