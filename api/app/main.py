"""main.py — Updated to register ALL V2 routers including new ones.

CHANGES from original:
- Added: auth_routes (legacy, for register/login/refresh)
- Added: communication_v2 (announcements, comments, activity, invites, notification-settings)
- Added: stats_v2 (leaderboard, rankings, achievements, highlights, player stats, group stats)
- Added: social_v2_extended (posts, friends, ratings, nearby, player profile, network, feed)
- Added: players_v2 (list/create players)
- Added: teams_v2 (CRUD teams)
- Added: avatars_v2 (presign, save)

EXISTING (unchanged):
- foundation_router, groups_v2, matches_v2, finance_v2, social_v2, notifications_v2, ranking_v2, profile_v2, health
"""
from __future__ import annotations

import os
import time

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.core.config import settings
from app.core.api_errors import error_payload, error_response, normalize_http_message, with_request_id
from app.core.logging import configure_logging, log_event
from app.core.db import get_session_local
from app.migrate import run_migrations_once

# ── Existing V2 routes ───────────────────────────────────────────────
from app.routes.foundation import router as foundation_router
from app.routes.groups_v2 import router as groups_v2_router
from app.routes.matches_v2 import router as matches_v2_router
from app.routes.finance_v2 import router as finance_v2_router, quick_router as finance_v2_quick_router
from app.routes.social_v2 import router as social_v2_router
from app.routes.notifications_v2 import router as notifications_v2_router
from app.routes.ranking_v2 import router as ranking_v2_router
from app.routes.health import router as health_router
from app.routes.profile_v2 import router as profile_v2_router

# ── NEW routes for frontend antigo support ───────────────────────────
from app.auth_routes import router as auth_router
from app.routes.communication_v2 import router as communication_v2_router
from app.routes.stats_v2 import router as stats_v2_router
from app.routes.social_v2_extended import router as social_v2_extended_router
from app.routes.players_v2 import router as players_v2_router
from app.routes.teams_v2 import router as teams_v2_router
from app.routes.avatars_v2 import router as avatars_v2_router
from app.routes.matches_v2_extended import router as matches_v2_extended_router

logger = configure_logging()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Register ALL routers
for router in [
    # Existing V2
    foundation_router,
    groups_v2_router,
    matches_v2_router,
    finance_v2_router,
    finance_v2_quick_router,
    social_v2_router,
    notifications_v2_router,
    ranking_v2_router,
    profile_v2_router,
    health_router,
    # NEW for frontend antigo
    auth_router,
    communication_v2_router,
    stats_v2_router,
    social_v2_extended_router,
    players_v2_router,
    teams_v2_router,
    avatars_v2_router,
    matches_v2_extended_router,
]:
    app.include_router(router)

_MIGRATIONS_STATE = {"ready": False, "error": None}
BUILD_SHA = settings.build_sha


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    detail = exc.detail
    if isinstance(detail, dict) and {"code", "message", "details"}.issubset(detail.keys()):
        payload = dict(detail)
        payload["details"] = with_request_id(payload.get("details"), request_id)
    else:
        payload = error_payload(
            code="http_error",
            message=normalize_http_message(exc.status_code, str(detail or "")),
            details=with_request_id({"path": request.url.path, "method": request.method, "raw_detail": str(detail or "")}, request_id),
        )
    log_event(logger, "http_exception", request_id=request_id, method=request.method, path=request.url.path, status_code=exc.status_code, code=payload.get("code"), raw_detail=str(detail or ""))
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    log_event(logger, "request_validation_error", request_id=request_id, method=request.method, path=request.url.path, errors_count=len(exc.errors()))
    return error_response(
        422,
        code="validation_error",
        message="Os dados enviados são inválidos.",
        details=with_request_id({"errors": exc.errors(), "path": request.url.path, "method": request.method}, request_id),
    )




@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception):
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    log_event(logger, "unexpected_exception", request_id=request_id, method=request.method, path=request.url.path, error_type=exc.__class__.__name__, error=str(exc))
    return error_response(
        500,
        code="internal_error",
        message="Ocorreu uma falha interna. Tente novamente em instantes.",
        details=with_request_id({"path": request.url.path, "method": request.method}, request_id),
    )


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = str(uuid4())
    request.state.request_id = request_id
    start = time.time()
    response = await call_next(request)
    elapsed = round((time.time() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{elapsed}ms"
    response.headers["X-API-Version"] = settings.app_version
    response.headers["X-Build-Sha"] = BUILD_SHA
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    if elapsed > 3000:
        log_event(logger, "slow_request", request_id=request_id, method=request.method, path=request.url.path, elapsed_ms=elapsed)
    return response


@app.on_event("startup")
async def startup():
    try:
        run_migrations_once()
        _MIGRATIONS_STATE["ready"] = True
    except Exception as exc:
        _MIGRATIONS_STATE["error"] = str(exc)
        logger.error("Migration failed: %s", exc)
