from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import time
from typing import Any


@dataclass
class _CacheItem:
    value: Any
    expires_at: float


class SimpleTtlCache:
    def __init__(self) -> None:
        self._items: dict[str, _CacheItem] = {}
        self._lock = RLock()

    def get(self, key: str) -> Any | None:
        now = time()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            if item.expires_at <= now:
                self._items.pop(key, None)
                return None
            return item.value

    def set(self, key: str, value: Any, ttl_seconds: int) -> Any:
        with self._lock:
            self._items[key] = _CacheItem(value=value, expires_at=time() + max(ttl_seconds, 1))
        return value

    def get_or_set(self, key: str, loader, ttl_seconds: int):
        cached = self.get(key)
        if cached is not None:
            return cached
        value = loader()
        return self.set(key, value, ttl_seconds)

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        with self._lock:
            keys = [key for key in self._items.keys() if key.startswith(prefix)]
            for key in keys:
                self._items.pop(key, None)


app_cache = SimpleTtlCache()
