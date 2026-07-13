import time
from collections import defaultdict


class RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Tracks request timestamps per key (typically a client IP) and
    rejects requests that exceed the configured limit within the
    rolling window. Designed for single-process deployments.
    """

    def __init__(self, max_requests: int, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        """Return True if the request is allowed, False if rate-limited."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        hits = self._hits[key]

        while hits and hits[0] < cutoff:
            hits.pop(0)

        if len(hits) >= self.max_requests:
            return False

        hits.append(now)
        return True

    def cleanup(self) -> None:
        """Remove stale keys whose timestamps are all outside the window."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        stale_keys = [key for key, hits in self._hits.items() if not hits or hits[-1] < cutoff]
        for key in stale_keys:
            del self._hits[key]
