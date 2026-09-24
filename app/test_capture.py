"""Offline tests for the capture transport (cards.capture_urls) — the sync
POST /api/ingest path and the INGEST_ASYNC=1 enqueue-and-poll path (ADR-0004).
httpx is replaced by a scripted fake; nothing touches the network.
"""

from types import SimpleNamespace

import httpx
import pytest

import bot
import cards
from tenant_auth import tenant_headers

pytestmark = pytest.mark.asyncio


class _Resp:
    def __init__(self, payload, status=200, headers=None):
        self._payload, self.status_code = payload, status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    """Scripted stand-in for httpx.AsyncClient: POST answers with `post_reply`,
    each GET pops the next payload from `gets`."""

    def __init__(self, post_reply, gets=()):
        self.post_reply, self.gets, self.calls, self.headers = post_reply, list(gets), [], []

    def __call__(self, **kwargs):          # httpx.AsyncClient(timeout=...) → self
        self.calls.append(("init", kwargs))
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json))
        self.headers.append(headers)
        return self.post_reply

    async def get(self, url, headers=None):
        self.calls.append(("GET", url))
        self.headers.append(headers)
        item = self.gets.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, _Resp) else _Resp(item)


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("SPOTBOT_SIGNING_KEY", "0" * 64)


TIMINGS: list[tuple] = []
REAL_REPORT = cards.report_time_to_card                     # before the fixture swaps it out


@pytest.fixture(autouse=True)
def _record_timings(monkeypatch):
    """handle_reel_capture reports time-to-card (#18); keep that off the fakes and the network."""
    TIMINGS.clear()

    async def record(guild, seconds, outcome, links):
        TIMINGS.append((guild, outcome, links))

    monkeypatch.setattr(cards, "report_time_to_card", record)


def _job(state, stage, done=0, total=1, **extra):
    return {"id": "j1", "state": state, "stage": stage,
            "progress": {"done": done, "total": total}, "result": None, "error": None, **extra}


async def test_sync_path_posts_to_ingest(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", False)
    fake = _FakeClient(_Resp({"events": [{"id": "e1"}], "added": 1}))
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    data = await cards.capture_urls(["https://www.instagram.com/reel/A/"], "g1", "u1")

    assert data["added"] == 1
    assert [c[1] for c in fake.calls if c[0] == "POST"] == [f"{cards.ADMIN_URL}/api/ingest"]
    assert fake.calls[1][2] == {
        "urls": ["https://www.instagram.com/reel/A/"],
        "guild_id": "g1",
        "user_id": "u1",
    }
    assert fake.headers == [tenant_headers("g1", user_id="u1")]


async def test_async_path_polls_reports_stages_and_returns_result(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    urls = ["https://www.instagram.com/reel/A/", "https://www.instagram.com/reel/B/"]
    result = {"events": [{"id": "e1"}, {"id": "e2"}], "added": 2}
    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued"}, 202),
        gets=[
            _job("queued", "queued", 0, 2),
            _job("running", "fetching", 0, 2),
            _job("running", "transcribing", 0, 2),
            _job("running", "transcribing", 0, 2),      # unchanged → no extra progress edit
            _job("running", "fetching", 1, 2),          # second URL started
            _job("done", "done", 2, 2, result=result),
        ],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)
    seen = []

    async def progress(text):
        seen.append(text)

    data = await cards.capture_urls(urls, "g1", "u1", progress=progress)

    assert data == result
    assert fake.calls[1] == (
        "POST",
        f"{cards.ADMIN_URL}/api/jobs",
        {"urls": urls, "guild_id": "g1", "user_id": "u1"},
    )
    assert all(c[1] == f"{cards.ADMIN_URL}/api/jobs/j1" for c in fake.calls if c[0] == "GET")
    assert fake.headers[0] == tenant_headers("g1", user_id="u1")
    assert all(header == tenant_headers("g1") for header in fake.headers[1:])
    assert seen == [
        "⏳ Waiting for a free capture slot… (0/2 done)",
        "🔎 Reading those 2 links — caption and location… (0/2 done)",
        "🎙️ Listening to the audio… (0/2 done)",
        "🔎 Reading those 2 links — caption and location… (1/2 done)",
    ]


async def test_async_path_raises_capture_failed_on_a_failed_job(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued"}, 202),
        gets=[_job("failed", "extracting", error="RuntimeError: chromium crashed")],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    with pytest.raises(cards.CaptureFailed, match="chromium crashed"):
        await cards.capture_urls(["https://www.instagram.com/reel/A/"], "g1", "u1")


async def test_full_queue_gets_its_own_bot_message(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    fake = _FakeClient(_Resp({"detail": "capture queue is full"}, 429))
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    class Status:
        def __init__(self):
            self.edits = []

        async def edit(self, **kwargs):
            self.edits.append(kwargs)

    status = Status()
    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=7, display_name="nick"),
    )

    async def reply(*args, **kwargs):
        return status

    async def no_reaction(*args, **kwargs):
        return None

    message.reply = reply
    monkeypatch.setattr(bot, "_add_reaction", no_reaction)
    monkeypatch.setattr(bot, "_swap_reaction", no_reaction)

    await bot.handle_reel_capture(message, ["https://www.instagram.com/reel/A/"])

    assert status.edits[-1]["content"] == (
        "⚠️ Lots of captures in line — try again in a minute."
    )


async def test_rate_limit_response_is_not_called_a_full_queue(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    fake = _FakeClient(
        _Resp(
            {"detail": "capture rate limit exceeded (user)"},
            429,
            headers={"Retry-After": "600"},
        )
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    with pytest.raises(cards.CaptureRateLimited, match="capture limit.*try again in 10 minutes"):
        await cards.capture_urls(["https://x.test/1"], "g1", "u1")


async def test_the_sync_path_tells_a_rate_limit_apart_too(monkeypatch):
    """INGEST_ASYNC=0 used to turn a 429 into 'couldn't reach the catalog'."""
    monkeypatch.setattr(cards, "INGEST_ASYNC", False)
    limited = _FakeClient(_Resp({"detail": "limit"}, 429, headers={"Retry-After": "30"}))
    monkeypatch.setattr(cards.httpx, "AsyncClient", limited)
    with pytest.raises(cards.CaptureRateLimited, match="try again in a minute"):
        await cards.capture_urls(["https://x.test/1"], "g1", "u1")

    full = _FakeClient(_Resp({"detail": "queue is full"}, 429))
    monkeypatch.setattr(cards.httpx, "AsyncClient", full)
    with pytest.raises(cards.CaptureQueueFull):
        await cards.capture_urls(["https://x.test/1"], "g1", "u1")


async def test_retry_after_reads_as_plain_time():
    assert cards._wait_phrase(30) == "in a minute"
    assert cards._wait_phrase(600) == "in 10 minutes"
    assert cards._wait_phrase(601) == "in 11 minutes"            # rounds up, never early
    assert cards._wait_phrase(3 * 3600) == "in about 3 hours"
    assert cards._wait_phrase(86400) == "in about 24 hours"


async def test_one_poll_timeout_is_tolerated(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    request = httpx.Request("GET", f"{cards.ADMIN_URL}/api/jobs/j1")
    result = {"events": [{"id": "e1"}], "added": 1}
    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued"}, 202),
        gets=[
            httpx.ReadTimeout("poll timed out", request=request),
            _job("done", "done", 1, 1, result=result),
        ],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    assert await cards.capture_urls(["https://x.test/1"], "g1", "u1") == result
    assert fake.headers[0] == tenant_headers("g1", user_id="u1")
    assert all(header == tenant_headers("g1") for header in fake.headers[1:])


async def test_one_poll_5xx_is_tolerated(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    result = {"events": [], "added": 0}
    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued"}, 202),
        gets=[
            _Resp({"detail": "restarting"}, 503),
            _job("done", "done", 1, 1, result=result),
        ],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    assert await cards.capture_urls(["https://x.test/1"], "g1", "u1") == result


async def test_three_quick_poll_errors_are_tolerated(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 1)
    monkeypatch.setattr(cards, "JOB_POLL_ERROR_S", 60)
    clock = {"now": 0.0}

    def monotonic():
        return clock["now"]

    async def advance(seconds):
        clock["now"] += seconds

    result = {"events": [], "added": 0}
    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued"}, 202),
        gets=[
            _Resp({}, 503),
            _Resp({}, 503),
            _Resp({}, 503),
            _job("done", "done", 1, 1, result=result),
        ],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)
    monkeypatch.setattr(cards.time, "monotonic", monotonic)
    monkeypatch.setattr(cards.asyncio, "sleep", advance)

    assert await cards.capture_urls(["https://x.test/1"], "g1", "u1") == result


async def test_poll_outage_fails_after_time_budget(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 20)
    monkeypatch.setattr(cards, "JOB_POLL_ERROR_S", 60)
    clock = {"now": 0.0}

    def monotonic():
        return clock["now"]

    async def advance(seconds):
        clock["now"] += seconds

    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued"}, 202),
        gets=[_Resp({}, 503), _Resp({}, 503), _Resp({}, 503), _Resp({}, 503)],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)
    monkeypatch.setattr(cards.time, "monotonic", monotonic)
    monkeypatch.setattr(cards.asyncio, "sleep", advance)

    with pytest.raises(cards.CapturePollingFailed, match="may still be running"):
        await cards.capture_urls(["https://x.test/1"], "g1", "u1")

    assert clock["now"] == 80.0


async def test_poll_404_uses_plain_user_message(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued"}, 202),
        gets=[_Resp({"detail": "job not found"}, 404)],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    with pytest.raises(
        cards.CaptureLost,
        match="The catalog restarted and lost this capture — tap Retry",
    ):
        await cards.capture_urls(["https://x.test/1"], "g1", "u1")


async def test_lost_job_logs_operator_storage_detail(monkeypatch, caplog):
    class Status:
        async def edit(self, **kwargs):
            return None

    async def reply(*args, **kwargs):
        return Status()

    async def no_reaction(*args, **kwargs):
        return None

    async def lost(*args, **kwargs):
        raise cards.CaptureLost("The catalog restarted and lost this capture — tap Retry.")

    message = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=7, display_name="nick"),
        reply=reply,
    )
    monkeypatch.setattr(bot, "_add_reaction", no_reaction)
    monkeypatch.setattr(bot, "_swap_reaction", no_reaction)
    monkeypatch.setattr(cards, "capture_urls", lost)

    await bot.handle_reel_capture(message, ["https://www.instagram.com/reel/A/"])

    assert "enable JOB_STORE=sqlite" in caplog.text
    assert TIMINGS == [("1", "error", 1)]                  # a failure is timed too (#18)


def _paste(monkeypatch, result):
    """A paste whose capture returns `result`, with the Discord side stubbed out."""
    class Status:
        async def edit(self, **kwargs):
            return None

    async def reply(*args, **kwargs):
        return Status()

    async def nothing(*args, **kwargs):
        return None

    async def capture(*args, **kwargs):
        return result

    monkeypatch.setattr(bot, "_add_reaction", nothing)
    monkeypatch.setattr(bot, "_swap_reaction", nothing)
    monkeypatch.setattr(bot, "_maybe_first_card_tip", nothing)
    monkeypatch.setattr(cards, "home_for", nothing)
    monkeypatch.setattr(cards, "capture_urls", capture)
    return SimpleNamespace(guild=SimpleNamespace(id=1), author=SimpleNamespace(id=7, display_name="nick"),
                           reply=reply)


async def test_a_paste_that_becomes_a_card_is_timed_as_a_card(monkeypatch):
    message = _paste(monkeypatch, {"events": [{"id": "e1", "venue": "Casa Loma", "category": "food_drink"}]})
    await bot.handle_reel_capture(message, ["https://www.instagram.com/reel/A/", "https://www.instagram.com/reel/B/"])
    assert TIMINGS == [("1", "card", 2)]


async def test_a_paste_with_no_venue_is_timed_as_no_card(monkeypatch):
    message = _paste(monkeypatch, {"events": [], "rejected": 1})
    await bot.handle_reel_capture(message, ["https://www.instagram.com/reel/A/"])
    assert TIMINGS == [("1", "no_card", 1)]


async def test_the_timing_report_carries_the_token_and_never_raises(monkeypatch):
    fake = _FakeClient(_Resp({"saved": True}))
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)
    await REAL_REPORT("g1", 5000.0, "card", 40)
    _, url, body = fake.calls[1]
    assert url == f"{cards.ADMIN_URL}/api/time-to-card"
    assert body == {"guild_id": "g1", "seconds": 3600.0, "outcome": "card", "links": 10}   # clamped
    assert fake.headers[0] == tenant_headers("g1")

    class Down(_FakeClient):
        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("admin app down")

    monkeypatch.setattr(cards.httpx, "AsyncClient", Down(None))
    await REAL_REPORT("g1", 5.0, "card", 1)                   # swallowed
    await REAL_REPORT("", 5.0, "card", 1)                     # no server, nothing sent


async def test_async_path_gives_up_after_the_wait_budget(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    monkeypatch.setattr(cards, "JOB_WAIT_S", 0)          # deadline already passed
    fake = _FakeClient(_Resp({"job_id": "j1", "state": "queued"}, 202), gets=[])
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    with pytest.raises(cards.CaptureFailed, match="did not finish"):
        await cards.capture_urls(["https://www.instagram.com/reel/A/"], "g1", "u1")


async def test_stage_line_wording():
    assert cards.stage_line(_job("running", "extracting"), 1) == "🧠 Working out the venue…"
    assert cards.stage_line(_job("running", "saving", 2, 3), 3) == "💾 Saving to the catalog… (2/3 done)"
    assert cards.stage_line({"state": "running"}, 1).startswith("🔎 Reading that reel")
    # The queue's own backoff stage (feature #26) gets its own words, not "Reading…".
    assert cards.stage_line(_job("running", "retrying"), 1) == "🔁 That timed out — trying again in a moment…"


async def test_a_slow_capture_says_it_is_still_working(monkeypatch):
    """Past the budget the user hears 'slow', not silence and not a failure."""
    monkeypatch.setattr(cards, "JOB_SLOW_AFTER_S", 180.0)
    assert "Still working" not in cards.stage_line(_job("running", "extracting"), 1, 179.0)
    slow = cards.stage_line(_job("running", "extracting"), 1, 245.0)
    assert slow.startswith("🧠 Working out the venue…")
    assert "Still working — 4 min so far" in slow


async def test_a_stalled_stage_still_refreshes_once_it_is_slow(monkeypatch):
    """The same stage for minutes must not leave the message frozen."""
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    monkeypatch.setattr(cards, "JOB_SLOW_AFTER_S", 0.0)     # every poll counts as slow
    monkeypatch.setattr(cards, "JOB_WAIT_S", 10_000.0)
    ticking = {"t": 0.0}                                    # a minute per reading

    def fake_monotonic():
        ticking["t"] += 61.0
        return ticking["t"]

    monkeypatch.setattr(cards.time, "monotonic", fake_monotonic)
    result = {"events": [], "added": 0}
    fake = _FakeClient(
        _Resp({"job_id": "j1", "state": "queued", "duplicate": False}, 202),
        gets=[
            _job("running", "extracting"),      # same stage…
            _job("running", "extracting"),      # …three polls running
            _job("done", "done", 1, 1, result=result),
        ],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)
    seen = []

    async def progress(text):
        seen.append(text)

    assert await cards.capture_urls(
        ["https://x.test/1"], "g1", "u1", progress=progress
    ) == result
    assert len(seen) == 2                        # refreshed although the stage never moved
    assert all("Still working" in line for line in seen)
    assert seen[0] != seen[1]                    # the minute count moved


async def test_retry_follows_the_job_the_server_gives_back(monkeypatch):
    """The server returns the running job's id for a duplicate paste; the bot
    polls that job instead of starting a second capture (feature #25)."""
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    result = {"events": [{"id": "e1"}], "added": 1}
    fake = _FakeClient(
        _Resp({"job_id": "already-running", "state": "running", "duplicate": True}, 202),
        gets=[_job("done", "done", 1, 1, result=result)],
    )
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    assert await cards.capture_urls(["https://x.test/1"], "g1", "u1") == result
    assert [c[1] for c in fake.calls if c[0] == "GET"] == [
        f"{cards.ADMIN_URL}/api/jobs/already-running"
    ]
