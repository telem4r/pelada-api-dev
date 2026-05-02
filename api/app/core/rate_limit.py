from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import Deque

from fastapi import Request

from app.core.api_errors import api_error


@dataclass
class _Bucket:
    hits: Deque[float]


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = RLock()

    def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.time()
        cutoff = now - max(window_seconds, 1)
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket(hits=deque()))
            while bucket.hits and bucket.hits[0] <= cutoff:
                bucket.hits.popleft()
            if len(bucket.hits) >= max(limit, 1):
                retry_after = int(max(bucket.hits[0] + window_seconds - now, 1)) if bucket.hits else window_seconds
                raise api_error(
                    429,
                    code="rate_limited",
                    message="Muitas tentativas em pouco tempo. Aguarde alguns instantes e tente novamente.",
                    details={"retry_after_seconds": retry_after},
                )
            bucket.hits.append(now)


rate_limiter = InMemoryRateLimiter()


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded_for:
        return forwarded_for
    client = getattr(request, "client", None)
    if client and client.host:
        return client.host
    return "unknown"


def consume_rate_limit(request: Request, *, scope: str, limit: int, window_seconds: int) -> None:
    identity = _client_ip(request)
    key = f"{scope}:{identity}"
    rate_limiter.hit(key, limit=limit, window_seconds=window_seconds)


def consume_rate_limit_key(*, key: str, limit: int, window_seconds: int) -> None:
    rate_limiter.hit(key, limit=limit, window_seconds=window_seconds)
