import os
import time
import logging
import zlib

import psycopg2
from alembic import command
from alembic.config import Config

logger = logging.getLogger("app.migrate")


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", "postgresql://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _advisory_lock_id(key: str) -> int:
    return zlib.crc32(key.encode("utf-8"))


def run_migrations_once(
    alembic_ini_path: str = "alembic.ini",
    lock_key: str = "borafut-alembic-migrations",
    wait_seconds: int = 60,
) -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL não definido (necessário para rodar migrations).")

    db_url_psycopg2 = _normalize_db_url(db_url)

    lock_id = _advisory_lock_id(lock_key)
    logger.info("migration: connecting to db for advisory lock (id=%s)", lock_id)

    conn = psycopg2.connect(db_url_psycopg2)
    conn.autocommit = True

    try:
        acquired = False
        deadline = time.time() + wait_seconds

        while time.time() < deadline:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s);", (lock_id,))
                acquired = bool(cur.fetchone()[0])

            if acquired:
                break

            logger.info("migration: another instance is migrating; waiting...")
            time.sleep(2)

        if not acquired:
            logger.warning("migration: could not acquire lock in time; skipping migrations.")
            return

        logger.info("migration: lock acquired; running alembic upgrade heads")

        cfg = Config(alembic_ini_path)

        # Alembic ConfigParser interpreta % (interpolation). Escapar evita erro com %21 etc.
        cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

        # ✅ IMPORTANTE: heads (plural)
        command.upgrade(cfg, "heads")

        logger.info("migration: upgrade heads finished successfully")
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s);", (lock_id,))
        except Exception:
            logger.exception("migration: failed to release advisory lock (ignored)")
        conn.close()
