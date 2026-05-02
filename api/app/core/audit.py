from __future__ import annotations

import logging
from typing import Any

from app.core.logging import log_event


def audit_admin_action(
    logger: logging.Logger,
    *,
    action: str,
    actor_user_id: int | None,
    group_id: str | None = None,
    match_id: int | None = None,
    target_user_id: int | None = None,
    target_player_id: int | None = None,
    target_request_id: int | None = None,
    outcome: str = "success",
    **extra: Any,
) -> None:
    log_event(
        logger,
        "admin_audit",
        action=action,
        actor_user_id=actor_user_id,
        group_id=group_id,
        match_id=match_id,
        target_user_id=target_user_id,
        target_player_id=target_player_id,
        target_request_id=target_request_id,
        outcome=outcome,
        **extra,
    )
