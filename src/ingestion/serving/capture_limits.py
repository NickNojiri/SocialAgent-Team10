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
        """(name, limit, window, table, key) for each limit that is switched on.

        A disabled limit yields nothing, so it never allocates a counter.
        """
        for name, limit, window_s, table, key in (
            ("user", self.user_limit, self.user_window_s, self._users, (guild_id, user_id)),
            ("server", self.server_limit, self.server_window_s, self._servers, guild_id),
            ("daily", self.daily_limit, self.daily_window_s, self._daily, guild_id),
        ):
            if limit:
                yield name, limit, window_s, table, key

    def _live(self, table: dict, key, now: float, window_s: float) -> deque[float]:
        """The bucket for `key` with expired entries dropped; forgotten once empty,
        so a server or user who stops pasting stops costing memory."""
        bucket = table.get(key)
        if bucket is None:
            return deque()
        self._prune(bucket, now, window_s)
        if not bucket:
            del table[key]
        return bucket

    def check(self, guild_id: str, user_id: str, *, amount: int = 1) -> None:
        """Raise before recording when any configured limit would be exceeded."""
        amount = max(1, int(amount))
        now = self._clock()
        for name, limit, window_s, table, key in self._limits(str(guild_id), str(user_id)):
            bucket = self._live(table, key, now, window_s)
            if len(bucket) + amount > limit:
                raise CaptureRateLimitExceeded(
                    name, self._retry_after(bucket, amount, limit, now, window_s)
                )

    def record(self, guild_id: str, user_id: str, *, amount: int = 1) -> None:
        """Record an accepted request after all other admission checks pass."""
        amount = max(1, int(amount))
        now = self._clock()
        for _name, _limit, window_s, table, key in self._limits(str(guild_id), str(user_id)):
            self._live(table, key, now, window_s)
            table.setdefault(key, deque()).extend([now] * amount)

    def tracked(self) -> int:
        """How many counters are held right now (for tests and /dash)."""
        return len(self._users) + len(self._servers) + len(self._daily)

    def settings(self) -> dict:
        """Which limits are on, and their windows — for the /dash operator view."""
        return {
            "user": {"limit": self.user_limit, "window_s": self.user_window_s},
            "server": {"limit": self.server_limit, "window_s": self.server_window_s},
            "daily": {"limit": self.daily_limit, "window_s": self.daily_window_s},
            "counters_held": self.tracked(),
        }
