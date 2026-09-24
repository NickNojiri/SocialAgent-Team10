"""In-memory capture rate limits for the single-process admin service."""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable


class CaptureRateLimitExceeded(RuntimeError):
    def __init__(self, limit_name: str, retry_after_s: int):
        super().__init__(limit_name)
        self.limit_name = limit_name
        self.retry_after_s = max(1, int(retry_after_s))


class CaptureRateLimiter:
    """Sliding-window counters keyed by signed user and server identities.

    A zero count disables that limit. State is intentionally in memory: the
    admin service runs as one process, and a restart safely clears the counters.
    """

    def __init__(
        self,
        *,
        user_limit: int = 0,
        user_window_s: float = 600.0,
        server_limit: int = 0,
        server_window_s: float = 3600.0,
        daily_limit: int = 0,
        daily_window_s: float = 86400.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_limit = max(0, int(user_limit))
        self.user_window_s = max(1.0, float(user_window_s))
        self.server_limit = max(0, int(server_limit))
        self.server_window_s = max(1.0, float(server_window_s))
        self.daily_limit = max(0, int(daily_limit))
        self.daily_window_s = max(1.0, float(daily_window_s))
        self._clock = clock
        self._users: dict[tuple[str, str], deque[float]] = {}
        self._servers: dict[str, deque[float]] = {}
        self._daily: dict[str, deque[float]] = {}

    @staticmethod
    def _prune(bucket: deque[float], now: float, window_s: float) -> None:
        cutoff = now - window_s
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    @staticmethod
    def _retry_after(
        bucket: deque[float], amount: int, limit: int, now: float, window_s: float
    ) -> int:
        overflow = len(bucket) + amount - limit
        if not bucket or overflow > len(bucket):
            return math.ceil(window_s)
        return max(1, math.ceil(bucket[overflow - 1] + window_s - now))

    def _limits(self, guild_id: str, user_id: str):
        return (
            ("user", self.user_limit, self.user_window_s,
             self._users.setdefault((guild_id, user_id), deque())),
            ("server", self.server_limit, self.server_window_s,
             self._servers.setdefault(guild_id, deque())),
            ("daily", self.daily_limit, self.daily_window_s,
             self._daily.setdefault(guild_id, deque())),
        )

    def check(self, guild_id: str, user_id: str, *, amount: int = 1) -> None:
        """Raise before recording when any configured limit would be exceeded."""
        amount = max(1, int(amount))
        now = self._clock()
        for name, limit, window_s, bucket in self._limits(str(guild_id), str(user_id)):
            self._prune(bucket, now, window_s)
            if limit and len(bucket) + amount > limit:
                raise CaptureRateLimitExceeded(
                    name, self._retry_after(bucket, amount, limit, now, window_s)
                )

    def record(self, guild_id: str, user_id: str, *, amount: int = 1) -> None:
        """Record an accepted request after all other admission checks pass."""
        amount = max(1, int(amount))
        now = self._clock()
        for _name, limit, window_s, bucket in self._limits(str(guild_id), str(user_id)):
            self._prune(bucket, now, window_s)
            if limit:
                bucket.extend([now] * amount)
