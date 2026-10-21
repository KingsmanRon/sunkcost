"""Token bucket per API key, in memory per instance (a documented v1 limitation, §4 C6)."""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self._rate = per_minute / 60.0
        self._burst = float(max(1, per_minute))
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float | None:
        """None if allowed; otherwise the seconds to wait before retrying."""
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (self._burst, now))
            tokens = min(self._burst, tokens + (now - last) * self._rate)
            if tokens >= 1.0:
                self._buckets[key] = (tokens - 1.0, now)
                return None
            self._buckets[key] = (tokens, now)
            return (1.0 - tokens) / self._rate
