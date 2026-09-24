"""/dash capture-health panels and /api/stats (feature #29).

/dash and /api/stats are an unauthenticated, cross-server operator view, bound to
localhost. So they may show counts and seconds and nothing else — the most
important test here is the one that plants a URL, a venue and an error message in
every source the view reads and checks none of them come back.
"""

import json

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
from src.ingestion.config import IngestionSettings
from src.ingestion.serving import capture_stats
from src.ingestion.serving.capture_limits import CaptureRateLimiter
from src.ingestion.serving.jobs import JobQueue, capture_key
from src.ingestion.sinks.chroma_sink import ChromaSink

SECRET_URL = "https://www.instagram.com/reel/SECRETPOST123/"
SECRET_VENUE = "Hidden Taqueria"
SECRET_ERROR = "ValueError: caption mentioned Hidden Taqueria"


def fake_embedder(texts):
    return [[float(len(t) % 7) for _ in range(8)] for t in texts]


async def _boom(urls, guild_id, on_stage):
    raise ValueError(SECRET_ERROR.split(": ", 1)[1])


@pytest.fixture()
def stats(tmp_path, monkeypatch):
    """GET /api/stats with every source seeded, some of it with identifying text."""
    sink = ChromaSink(IngestionSettings(chroma_path=str(tmp_path)), embedder=fake_embedder)
    monkeypatch.setitem(admin._sinks, "", sink)           # never touch the real data/

    log = tmp_path / "capture_jobs.jsonl"
    key = capture_key("g1", SECRET_URL)
    rows = [
        {"state": "done", "urls": 1, "url_keys": [key], "duration_s": 40.0,
         "stages": {"fetching": 30.0, "extracting": 10.0}},
        {"state": "done", "urls": 1, "url_keys": [key], "duration_s": 200.0,
         "stages": {"fetching": 190.0, "extracting": 10.0}},
        {"state": "failed", "urls": 1, "url_keys": ["other"], "duration_s": 400.0,
         "stages": {"fetching": 400.0}, "error": SECRET_ERROR},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    queue = JobQueue(_boom, log_path=log, max_queued=7, backoff_base_s=0)
    monkeypatch.setattr(admin, "_jobs", queue)
    monkeypatch.setattr(admin, "_capture_limits",
                        CaptureRateLimiter(user_limit=5, user_window_s=600))
    admin._CAPTURE_LOG.appendleft({
        "ts": 1_800_000_000, "guild_id": "g1", "urls": 1, "added": 1, "rejected": 0,
        "unreadable": 0, "duration_s": 12.3,
        "lines": [f"[ok]      {SECRET_URL} -> '{SECRET_VENUE}' / food_drink"],
    })
    with TestClient(admin.app) as client:
        # a real failed job in the live queue too, with a URL and error text
        from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token
        monkeypatch.setenv(ENV_VAR, "0" * 64)
        client.post("/api/jobs", json={"urls": [SECRET_URL], "guild_id": "g1", "user_id": "u1"},
                    headers={"X-Tenant-Token": mint_token("g1", user_id="u1")})
        for _ in range(100):
            if queue.snapshot()["failed_recent"]:
                break
            import time
            time.sleep(0.02)
        resp = client.get("/api/stats")
    admin._CAPTURE_LOG.popleft()
    assert resp.status_code == 200
    return resp


def test_nothing_that_identifies_a_post_leaves_the_operator_view(stats):
    text = stats.text
    for secret in (SECRET_URL, "SECRETPOST123", SECRET_VENUE, "caption mentioned"):
        assert secret not in text, f"{secret!r} leaked into /api/stats"
    assert capture_key("g1", SECRET_URL) not in text        # not even the dedup hash
    assert all("lines" not in c for c in stats.json()["captures"])


def test_capture_health_counts_and_timings(stats):
    h = stats.json()["capture_health"]
    assert h["captures"] == 4                               # 3 seeded + the live failure
    assert h["states"]["done"] == 2 and h["states"]["failed"] == 2
    assert h["over_threshold"] == {"180s": 2, "300s": 1}
    assert h["duplicates"]["wasted_captures"] == 2          # SECRET_URL in g1: 2 seeded + the live job
    assert h["stage_median_s"]["extracting"] == 10.0


def test_queue_and_limits_are_reported_as_counts(stats):
    data = stats.json()
    assert data["queue"]["max_queued"] == 7
    assert data["queue"]["failed_recent"] == 1 and data["queue"]["durable"] is False
    assert data["queue"]["recent_window_s"] == 3600.0        # the page labels "failed (last 1h)" from this
    assert data["limits"]["user"] == {"limit": 5, "window_s": 600.0}
    assert data["limits"]["server"]["limit"] == 0          # off
    assert data["limits"]["counters_held"] == 1            # u1's one accepted capture


def test_capture_health_is_null_when_the_timing_log_is_off(tmp_path, monkeypatch):
    sink = ChromaSink(IngestionSettings(chroma_path=str(tmp_path)), embedder=fake_embedder)
    monkeypatch.setitem(admin._sinks, "", sink)
    monkeypatch.setattr(admin, "_jobs", JobQueue(_boom, log_path=None))
    with TestClient(admin.app) as client:
        assert client.get("/api/stats").json()["capture_health"] is None


def test_the_dash_page_has_the_new_panels():
    with TestClient(admin.app) as client:
        page = client.get("/dash").text
    for element in ('id="health"', 'id="stages"', 'id="queue"', 'id="security"'):
        assert element in page
    assert "#32" in page                                    # Track D's reserved slot


def test_reading_the_tail_of_a_big_log_skips_the_cut_line(tmp_path):
    log = tmp_path / "capture_jobs.jsonl"
    rows = [{"state": "done", "urls": 1, "url_keys": [f"k{i}"], "duration_s": float(i),
             "stages": {}} for i in range(200)]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    tail = capture_stats.read_rows(log, max_bytes=1000)
    assert 0 < len(tail) < 200
    assert tail[-1]["duration_s"] == 199.0                  # newest rows kept
    assert all("state" in r for r in tail)                  # no half-line parsed as a row
    assert len(capture_stats.read_rows(log)) == 200         # no limit → everything
