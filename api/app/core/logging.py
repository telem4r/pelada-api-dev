from __future__ import annotations

import json
import logging
from typing import Any


def configure_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    return logging.getLogger("app")


def log_event(logger: logging.Logger, event_type: str, **fields: Any) -> None:
    payload = {"event_type": event_type, **{k: v for k, v in fields.items() if v is not None}}
    logger.info(json.dumps(payload, default=str, ensure_ascii=False))
