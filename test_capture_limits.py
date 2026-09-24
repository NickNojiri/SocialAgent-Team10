"""CaptureRateLimiter on its own (feature #28) — windows, Retry-After, and memory.

The endpoint behaviour (429s, signed user ids, log lines) is in test_admin_authz.py.
"""

import pytest

from src.ingestion.serving.capture_limits import CaptureRateLimiter, CaptureRateLimitExceeded


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_a_limit_opens_again_as_its_window_slides():
    clock = Clock()
    lim = CaptureRateLimiter(user_limit=2, user_window_s=600, clock=clock)
    lim.record("g", "u")
    clock.t += 100
    lim.record("g", "u")
    with pytest.raises(CaptureRateLimitExceeded) as exc:
        lim.check("g", "u")
    assert exc.value.limit_name == "user"
    assert exc.value.retry_after_s == 500          # the first one ages out in 500 s, not 600

    clock.t += 500                                 # exactly when the first expires
    lim.check("g", "u")                            # open again


def test_users_and_servers_are_counted_separately():
    lim = CaptureRateLimiter(user_limit=1, server_limit=3, clock=Clock())
    lim.record("g1", "alice")
    lim.check("g1", "bob")                         # alice's use isn't bob's
    lim.check("g2", "alice")                       # nor alice's in another server
    with pytest.raises(CaptureRateLimitExceeded):
        lim.check("g1", "alice")


def test_disabled_limits_hold_no_counters():
    """All limits ship at 0. With nothing switched on, nothing is remembered."""
    lim = CaptureRateLimiter(clock=Clock())
    for i in range(1000):
        lim.check(f"g{i}", f"u{i}")
        lim.record(f"g{i}", f"u{i}")
    assert lim.tracked() == 0


def test_a_server_that_goes_quiet_stops_costing_memory():
    clock = Clock()
    lim = CaptureRateLimiter(user_limit=5, server_limit=5, clock=clock)
    for i in range(50):
        lim.record(f"g{i}", "u")
    assert lim.tracked() == 100                    # a user and a server counter each
    clock.t += 3601                                # past both windows
    for i in range(50):
        lim.check(f"g{i}", "u")
    assert lim.tracked() == 0


def test_check_never_records():
    lim = CaptureRateLimiter(user_limit=1, clock=Clock())
    for _ in range(5):
        lim.check("g", "u")                        # asking isn't using
    lim.record("g", "u")
    with pytest.raises(CaptureRateLimitExceeded):
        lim.check("g", "u")
