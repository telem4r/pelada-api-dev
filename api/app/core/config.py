
from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "BoraFut API")
    app_version: str = os.getenv("APP_VERSION", "3.0.0-supabase-foundation")
    env: str = os.getenv("ENV", "development")
    build_sha: str = os.getenv("BUILD_SHA", "local-dev")

    # Postgres (Supabase)
    database_url: str = os.getenv("DATABASE_URL", "")
    run_migrations_on_startup: bool = _as_bool(os.getenv("RUN_MIGRATIONS_ON_STARTUP", "0"), default=False)

    # Supabase
    supabase_url: str = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_anon_key: str = os.getenv("SUPABASE_ANON_KEY", "")
    supabase_service_role_key: str = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    supabase_jwt_audience: str = os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")

    # Legacy JWT kept only for controlled transition of old routes
    jwt_secret: str = os.getenv("JWT_SECRET", "")


settings = Settings()
