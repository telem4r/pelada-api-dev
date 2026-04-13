import os
import sys
import logging
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("app.run")


def _normalize_db_url(url: str) -> str:
    # SQLAlchemy prefere "postgresql://"
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def run_migrations() -> None:
    """
    Roda alembic upgrade head usando DATABASE_URL do ambiente.
    """
    base_dir = Path(__file__).resolve().parent.parent  # /app/api
    alembic_ini = base_dir / "alembic.ini"

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL não definido no ambiente do App Runner")

    db_url = _normalize_db_url(db_url)

    cfg = Config(str(alembic_ini))

    # Garantir caminhos corretos quando executado via App Runner
    cfg.set_main_option("script_location", str(base_dir / "alembic"))

    # Alembic ConfigParser interpreta % (interpolation). Escapar evita erro com %21 etc.
    cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

    logger.info("Running migrations (alembic upgrade head)...")
    command.upgrade(cfg, "head")
    logger.info("Migrations OK.")


def main() -> None:
    # 1) migrations
    run_migrations()

    # 2) start API
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))

    logger.info("Starting uvicorn on %s:%s ...", host, port)
    uvicorn.run("app.main:app", host=host, port=port, log_level=os.getenv("UVICORN_LOG_LEVEL", "info"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception("Startup failed: %s", e)
        sys.exit(1)
