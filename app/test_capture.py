"""Offline tests for the capture transport (cards.capture_urls) — the sync
POST /api/ingest path and the INGEST_ASYNC=1 enqueue-and-poll path (ADR-0004).
httpx is replaced by a scripted fake; nothing touches the network.
"""

import pytest

import cards
from tenant_auth import tenant_headers

pytestmark = pytest.mark.asyncio


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

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
        return _Resp(self.gets.pop(0))


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("SPOTBOT_SIGNING_KEY", "0" * 64)


def _job(state, stage, done=0, total=1, **extra):
    return {"id": "j1", "state": state, "stage": stage,
            "progress": {"done": done, "total": total}, "result": None, "error": None, **extra}


async def test_sync_path_posts_to_ingest(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", False)
    fake = _FakeClient(_Resp({"events": [{"id": "e1"}], "added": 1}))
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    data = await cards.capture_urls(["https://www.instagram.com/reel/A/"], "g1")

    assert data["added"] == 1
    assert [c[1] for c in fake.calls if c[0] == "POST"] == [f"{cards.ADMIN_URL}/api/ingest"]
    assert fake.calls[1][2] == {"urls": ["https://www.instagram.com/reel/A/"], "guild_id": "g1"}
    assert fake.headers == [tenant_headers("g1")]         # signed for the tenant it names


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

    data = await cards.capture_urls(urls, "g1", progress=progress)

    assert data == result
    assert fake.calls[1] == ("POST", f"{cards.ADMIN_URL}/api/jobs", {"urls": urls, "guild_id": "g1"})
    assert all(c[1] == f"{cards.ADMIN_URL}/api/jobs/j1" for c in fake.calls if c[0] == "GET")
    assert fake.headers and all(h == tenant_headers("g1") for h in fake.headers)   # submit and every poll
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
        await cards.capture_urls(["https://www.instagram.com/reel/A/"], "g1")


async def test_async_path_gives_up_after_the_wait_budget(monkeypatch):
    monkeypatch.setattr(cards, "INGEST_ASYNC", True)
    monkeypatch.setattr(cards, "JOB_POLL_S", 0)
    monkeypatch.setattr(cards, "JOB_WAIT_S", 0)          # deadline already passed
    fake = _FakeClient(_Resp({"job_id": "j1", "state": "queued"}, 202), gets=[])
    monkeypatch.setattr(cards.httpx, "AsyncClient", fake)

    with pytest.raises(cards.CaptureFailed, match="did not finish"):
        await cards.capture_urls(["https://www.instagram.com/reel/A/"], "g1")


async def test_stage_line_wording():
    assert cards.stage_line(_job("running", "extracting"), 1) == "🧠 Working out the venue…"
    assert cards.stage_line(_job("running", "saving", 2, 3), 3) == "💾 Saving to the catalog… (2/3 done)"
    assert cards.stage_line({"state": "running"}, 1).startswith("🔎 Reading that reel")
