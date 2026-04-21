"""TTL memoization.

RS, liquidity, and regime services all refetch the same SPY bars many times
per minute otherwise. This sits in front of DataProvider calls.
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable


class TTLCache:
    def __init__(self, default_ttl_seconds: float = 10.0) -> None:
        self._default_ttl = default_ttl_seconds
        self._store: dict[Any, tuple[float, Any]] = {}
        self._lock = Lock()

    def get_or_compute(
        self,
        key: Any,
        fn: Callable[[], Any],
        ttl: float | None = None,
    ) -> Any:
        ttl = ttl if ttl is not None else self._default_ttl
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
        value = fn()
        with self._lock:
            self._store[key] = (now + ttl, value)
        return value

    def invalidate(self, key: Any) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
