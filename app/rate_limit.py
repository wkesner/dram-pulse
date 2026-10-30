"""In-memory rate limits + fair concurrency gates (demo-grade, no Redis)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from threading import Lock


class RateLimiter:
    """Sliding-window limiter. Keys are independent — one busy client cannot
    exhaust another client's budget (avoids shared-bucket starvation)."""

    def __init__(self, *, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max_calls:
                return False
            q.append(now)
            return True

    def prune(self, *, max_keys: int = 4096) -> None:
        """Drop idle keys so long-lived processes do not grow unbounded."""
        now = time.monotonic()
        with self._lock:
            idle = [
                k
                for k, q in self._hits.items()
                if not q or now - q[-1] > self.window * 2
            ]
            for k in idle:
                del self._hits[k]
            if len(self._hits) <= max_keys:
                return
            # Evict oldest-idle first if still over cap.
            ranked = sorted(
                self._hits.items(),
                key=lambda kv: kv[1][-1] if kv[1] else 0.0,
            )
            for k, _ in ranked[: len(self._hits) - max_keys]:
                del self._hits[k]


class ConcurrencyGate:
    """Caps simultaneous expensive handlers so one slow client cannot pin the process."""

    def __init__(self, limit: int) -> None:
        self._sem = asyncio.Semaphore(max(1, limit))

    async def __aenter__(self) -> ConcurrencyGate:
        await self._sem.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._sem.release()
