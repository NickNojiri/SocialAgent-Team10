"""Offline tests for the async capture job queue (ADR-0004) — no browser, no
network: the capture function is faked, the endpoints run under TestClient.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.ingestion.serving.jobs import (
    JobQueue,
    MemoryJobStore,
    QueueFull,
    SqliteJobStore,
    capture_key,
    normalize_capture_url,
)

_spec = importlib.util.spec_from_file_location(
    "summarize_captures", Path(__file__).parent / "scripts" / "summarize_captures.py"
)
summarize_captures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(summarize_captures)

STAGES = ("fetching", "transcribing", "extracting", "saving", "done")


async def fake_run(urls, guild_id, on_stage):
    for url in urls:
        for stage in STAGES:
            on_stage(url, stage)
    return {"added": len(urls), "rejected": 0, "unreadable": 0,
            "events": [{"id": u, "venue": "X"} for u in urls], "log": []}


# ── JobQueue ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_job_runs_to_done_with_progress():
    q = JobQueue(fake_run)
    job = q.submit(["https://x.test/1", "https://x.test/2"], "g1")
    assert job.state == "queued"
    assert job.to_dict()["progress"] == {"done": 0, "total": 2}

    await q.drain()
    assert job.state == "done"
    assert job.stage == "done"
    assert job.result["added"] == 2
    assert job.to_dict()["progress"] == {"done": 2, "total": 2}
    assert job.finished_at is not None
    assert q.pending == 0


@pytest.mark.asyncio
async def test_failed_job_is_reported_and_the_worker_survives():
    async def boom(urls, guild_id, on_stage):
        raise ValueError("nope")

    q = JobQueue(boom)
    bad = q.submit(["https://x.test/1"])
    await q.drain()
    assert bad.state == "failed"
    assert bad.error == "ValueError: nope"
    assert bad.result is None

    q._run = fake_run                     # same worker task, next job succeeds
    good = q.submit(["https://x.test/2"])
    await q.drain()
    assert good.state == "done"


@pytest.mark.asyncio
async def test_queue_is_bounded_and_finished_jobs_expire():
    gate = asyncio.Event()

    async def slow(urls, guild_id, on_stage):
        await gate.wait()
        return {"added": 0, "events": []}

    q = JobQueue(slow, max_queued=1, ttl_s=10)
    running = q.submit(["https://x.test/1"])
    await asyncio.sleep(0)                # the worker picks it up → running, not waiting
    assert running.state == "running"
    waiting = q.submit(["https://x.test/2"])
    with pytest.raises(QueueFull):
        q.submit(["https://x.test/3"])

    gate.set()
    await q.drain()
    assert running.state == waiting.state == "done"
    assert q.sweep(now=time.time() + 11) == 2
    assert q.get(running.id) is None


@pytest.mark.asyncio
async def test_two_workers_run_two_jobs_at_once():
    started = asyncio.Event()
    release = asyncio.Event()
    active = []

    async def run(urls, guild_id, on_stage):
        active.append(urls[0])
        if len(active) == 2:
            started.set()
        await release.wait()
        return {"added": 1, "events": []}

    q = JobQueue(run, workers=2)
    q.submit(["a"]); q.submit(["b"])
    await asyncio.wait_for(started.wait(), timeout=2)   # both in flight together
    release.set()
    await q.drain()
    assert sorted(active) == ["a", "b"]


# ── Timing + capture statistics (feature #23) ───────────────────────────────


@pytest.mark.asyncio
async def test_job_records_time_per_stage():
    q = JobQueue(fake_run)
    job = q.submit(["https://x.test/1"], "g1")
    await q.drain()

    assert set(job.stage_times) >= set(STAGES), job.stage_times
    assert all(v >= 0 for v in job.stage_times.values())
    # The stages account for the whole job, give or take the scheduler.
    assert sum(job.stage_times.values()) == pytest.approx(job.duration_s, abs=0.5)
    timing = job.to_dict()["timing"]
    assert timing["duration_s"] >= 0 and "fetching" in timing["stages"]


@pytest.mark.asyncio
async def test_finished_jobs_are_appended_to_the_capture_log(tmp_path):
    log = tmp_path / "nested" / "capture_jobs.jsonl"
    q = JobQueue(fake_run, log_path=log)
    q.submit(["https://www.instagram.com/reel/AAA/?igsh=xyz"], "g1")
    q.submit(["https://x.test/2"], "g1")
    await q.drain()

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert {r["state"] for r in rows} == {"done"}
    assert all(r["duration_s"] >= 0 and r["stages"] for r in rows)
    # The log is for operations: links are identified by key, nothing secret rides along.
    assert all(len(r["url_keys"]) == r["urls"] for r in rows)
    assert "token" not in log.read_text(encoding="utf-8").lower()


@pytest.mark.asyncio
async def test_a_broken_capture_log_never_fails_a_job(tmp_path):
    blocked = tmp_path / "file.txt"
    blocked.write_text("not a directory", encoding="utf-8")
    q = JobQueue(fake_run, log_path=blocked / "capture_jobs.jsonl")
    job = q.submit(["https://x.test/1"], "g1")
    await q.drain()
    assert job.state == "done"          # the write failed; the capture did not


def test_normalized_links_and_keys_ignore_tracking_noise():
    plain = "https://www.instagram.com/reel/ABC123/"
    shared = "https://instagram.com/reel/ABC123?igsh=abc&utm_source=ig_web"
    assert normalize_capture_url(plain) == normalize_capture_url(shared)
    assert capture_key("g1", plain) == capture_key("g1", shared)
    # Different post, and the same post in a different server, are different keys.
    assert capture_key("g1", plain) != capture_key("g1", plain.replace("ABC123", "ZZZ999"))
    assert capture_key("g1", plain) != capture_key("g2", plain)
    assert normalize_capture_url("not a url") == "not a url"


def test_summarize_captures_reports_durations_and_duplicates(tmp_path):
    k1, k2 = capture_key("g1", "https://x.test/1"), capture_key("g1", "https://x.test/2")
    rows = [
        {"state": "done", "urls": 1, "url_keys": [k1], "duration_s": 12.0,
         "stages": {"fetching": 8.0, "extracting": 4.0}},
        {"state": "done", "urls": 1, "url_keys": [k1], "duration_s": 200.0,
         "stages": {"fetching": 190.0, "extracting": 10.0}},
        {"state": "failed", "urls": 1, "url_keys": [k2], "duration_s": 400.0,
         "stages": {"fetching": 400.0}},
    ]
    log = tmp_path / "capture_jobs.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n{truncated", encoding="utf-8")

    summary = summarize_captures.summarize(summarize_captures.read_rows(log))
    assert summary["captures"] == 3                      # the truncated line is skipped
    assert summary["states"] == {"done": 2, "failed": 1}
    assert summary["duration_s"]["max"] == 400.0
    assert summary["over_threshold"] == {"180s": 2, "300s": 1}
    assert summary["stage_median_s"]["fetching"] == 190.0
    assert summary["duplicates"] == {
        "distinct_links": 2, "links_captured_more_than_once": 1, "wasted_captures": 1,
    }
    assert "duplicates" in summarize_captures.render(summary)


def test_summarize_captures_handles_an_empty_log(tmp_path):
    summary = summarize_captures.summarize(summarize_captures.read_rows(tmp_path / "none.jsonl"))
    assert summary["captures"] == 0
    assert "No captures logged yet" in summarize_captures.render(summary)


# ── Durable store + crash recovery (feature #24) ────────────────────────────


@pytest.mark.asyncio
async def test_sqlite_store_survives_the_process_that_wrote_it(tmp_path):
    db = tmp_path / "state" / "jobs.db"
    q = JobQueue(fake_run, store=SqliteJobStore(db))
    job = q.submit(["https://x.test/1"], "g1")
    await q.drain()
    q.store.close()

    # A brand-new queue, as if the service had been restarted.
    reopened = JobQueue(fake_run, store=SqliteJobStore(db))
    row = reopened.get(job.id)
    assert row is not None and row.state == "done"
    assert row.urls == ["https://x.test/1"] and row.guild_id == "g1"
    assert row.result["added"] == 1
    assert reopened.recover() == {"requeued": 0, "failed": 0}   # nothing was interrupted


@pytest.mark.asyncio
async def test_a_crash_mid_job_is_retried_once_then_failed(tmp_path):
    db = tmp_path / "jobs.db"
    gate = asyncio.Event()

    async def never_finishes(urls, guild_id, on_stage):
        await gate.wait()
        return {"added": 0, "events": []}

    crashed = JobQueue(never_finishes, store=SqliteJobStore(db))
    job = crashed.submit(["https://x.test/1"], "g1")
    await asyncio.sleep(0)                        # the worker picks it up
    assert crashed.get(job.id).state == "running"
    crashed.shutdown()                            # the process dies here
    crashed.store.close()

    # Restart 1: the interrupted job gets one more attempt and runs to done.
    restart = JobQueue(fake_run, store=SqliteJobStore(db))
    assert restart.recover() == {"requeued": 1, "failed": 0}
    assert restart.get(job.id).state == "queued"
    restart.start()
    await restart.drain()
    assert restart.get(job.id).state == "done"
    restart.store.close()

    # A job that crashes again, having already used its one recovery, ends
    # failed — never queued forever. (Restarts are counted in `recoveries`,
    # separately from feature #26's retries.)
    store = SqliteJobStore(db)
    stuck = store.load(job.id)
    assert stuck.recoveries == 1
    stuck.state, stuck.finished_at = "running", None
    store.save(stuck)
    after = JobQueue(fake_run, store=store)
    assert after.recover() == {"requeued": 0, "failed": 1}
    done = after.get(job.id)
    assert done.state == "failed" and "restarted" in done.error
    assert done.finished_at is not None
    store.close()


@pytest.mark.asyncio
async def test_the_memory_store_is_the_default_and_forgets(tmp_path):
    q = JobQueue(fake_run)
    job = q.submit(["https://x.test/1"], "g1")
    await q.drain()
    assert isinstance(q.store, MemoryJobStore)
    assert q.store.load(job.id) is None
    assert q.recover() == {"requeued": 0, "failed": 0}
    assert not (tmp_path / "jobs.db").exists()


@pytest.mark.asyncio
async def test_sweeping_a_finished_job_clears_the_durable_copy_too(tmp_path):
    q = JobQueue(fake_run, ttl_s=10, store=SqliteJobStore(tmp_path / "jobs.db"))
    job = q.submit(["https://x.test/1"], "g1")
    await q.drain()
    assert q.sweep(now=time.time() + 11) == 1
    assert q.get(job.id) is None
    q.store.close()


# ── Idempotent submission (feature #25) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_same_link_twice_makes_one_job():
    gate = asyncio.Event()

    async def slow(urls, guild_id, on_stage):
        await gate.wait()
        return {"added": len(urls), "events": []}

    q = JobQueue(slow)
    first = q.submit(["https://www.instagram.com/reel/AAA/"], "g1")
    await asyncio.sleep(0)                       # first is running
    again = q.submit(["https://instagram.com/reel/AAA?igsh=noise"], "g1")   # same post
    assert again.id == first.id                  # the Retry path joins it

    other_server = q.submit(["https://www.instagram.com/reel/AAA/"], "g2")
    assert other_server.id != first.id           # different server, its own capture

    gate.set()
    await q.drain()
    # Once it has finished, pasting it again is a deliberate re-capture.
    assert q.submit(["https://www.instagram.com/reel/AAA/"], "g1").id != first.id


@pytest.mark.asyncio
async def test_a_partly_new_paste_only_captures_what_is_not_in_flight():
    gate = asyncio.Event()
    captured = []

    async def slow(urls, guild_id, on_stage):
        captured.append(list(urls))
        await gate.wait()
        return {"added": len(urls), "events": []}

    q = JobQueue(slow, workers=2)
    q.submit(["https://x.test/1"], "g1")
    await asyncio.sleep(0)
    second = q.submit(["https://x.test/1", "https://x.test/2"], "g1")
    assert second.urls == ["https://x.test/2"]   # link 1 is already being captured
    gate.set()
    await q.drain()
    assert sorted(sum(captured, [])) == ["https://x.test/1", "https://x.test/2"]


def test_job_endpoint_reports_a_duplicate_submission(monkeypatch):
    import src.ingestion.serving.admin as admin
    from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token

    monkeypatch.setenv(ENV_VAR, "0" * 64)
    auth = {"X-Tenant-Token": mint_token("g1")}

    async def slow(urls, guild_id, on_stage):
        await asyncio.sleep(0.3)
        return {"added": len(urls), "events": []}

    monkeypatch.setattr(admin, "_jobs", JobQueue(slow))
    with TestClient(admin.app) as client:
        body = {"urls": ["https://www.instagram.com/reel/AAA/"], "guild_id": "g1"}
        first = client.post("/api/jobs", json=body, headers=auth).json()
        second = client.post("/api/jobs", json=body, headers=auth).json()
        assert first["duplicate"] is False
        assert second["duplicate"] is True and second["job_id"] == first["job_id"]
        _wait_done(client, first["job_id"], headers=auth)


# ── Retry only what's worth retrying (feature #26) ──────────────────────────


def _timeouts_then(ok_after: int, added_each: int = 1):
    """A fake capture whose links time out `ok_after` times, then load."""
    calls = []

    async def run(urls, guild_id, on_stage):
        calls.append(list(urls))
        timed_out = list(urls) if len(calls) <= ok_after else []
        loaded = [u for u in urls if u not in timed_out]
        return {"added": added_each * len(loaded), "rejected": 0,
                "unreadable": len(timed_out), "retryable_urls": timed_out,
                "events": [{"id": u} for u in loaded], "log": [f"run {len(calls)}"]}

    return run, calls


def test_backoff_is_exponential_and_capped():
    q = JobQueue(fake_run, backoff_base_s=5, backoff_cap_s=60)
    assert [q.backoff_s(n) for n in (1, 2, 3, 4, 5)] == [5, 10, 20, 40, 60]


def test_only_network_shaped_errors_count_as_transient():
    import httpx
    from src.ingestion.serving.jobs import is_transient_error

    assert is_transient_error(TimeoutError("slow"))
    assert is_transient_error(asyncio.TimeoutError())
    assert is_transient_error(ConnectionResetError("dropped"))
    assert is_transient_error(httpx.ConnectError("refused"))
    assert not is_transient_error(ValueError("bad caption"))
    assert not is_transient_error(KeyError("venue"))
    assert not is_transient_error(RuntimeError("a bug"))


@pytest.mark.asyncio
async def test_a_timed_out_link_is_retried_alone_and_the_results_merge():
    calls = []
    urls = ["https://x.test/1", "https://x.test/2"]

    async def one_times_out(urls_, guild_id, on_stage):   # only link 2 times out, once
        calls.append(list(urls_))
        bad = ["https://x.test/2"] if len(calls) == 1 else []
        good = [u for u in urls_ if u not in bad]
        return {"added": len(good), "rejected": 0, "unreadable": len(bad),
                "retryable_urls": bad, "events": [{"id": u} for u in good], "log": []}

    q = JobQueue(one_times_out, backoff_base_s=0)
    job = q.submit(urls, "g1")
    await q.drain()

    assert calls == [urls, ["https://x.test/2"]]        # the retry ran link 2 only
    assert job.state == "done" and job.attempts == 2
    assert job.result["added"] == 2 and job.result["unreadable"] == 0
    assert [e["id"] for e in job.result["events"]] == urls
    assert job.last_error == "timed out loading 1 link(s)"   # kept after the retry healed it


@pytest.mark.asyncio
async def test_retries_stop_at_the_cap_and_the_job_still_finishes():
    run, calls = _timeouts_then(ok_after=99)
    q = JobQueue(run, max_retries=2, backoff_base_s=0)
    job = q.submit(["https://x.test/1"], "g1")
    await q.drain()

    assert len(calls) == 3 and job.attempts == 3        # first try + 2 retries, no more
    assert job.state == "done"                          # reported as unreadable, not failed
    assert job.result["unreadable"] == 1 and job.result["added"] == 0
    assert job.last_error == "timed out loading 1 link(s)"


@pytest.mark.asyncio
async def test_a_rejected_or_walled_link_is_never_retried():
    calls = []

    async def rejected(urls, guild_id, on_stage):
        calls.append(urls)
        # Read fine, extraction rejected it; a login wall is unreadable but not retryable.
        return {"added": 0, "rejected": 1, "unreadable": 1, "retryable_urls": [],
                "events": [], "log": []}

    q = JobQueue(rejected, backoff_base_s=0)
    job = q.submit(["https://x.test/1", "https://x.test/2"], "g1")
    await q.drain()
    assert len(calls) == 1 and job.attempts == 1 and job.last_error is None


@pytest.mark.asyncio
async def test_a_network_error_retries_the_job_and_a_bug_does_not():
    flaky_calls = []

    async def flaky(urls, guild_id, on_stage):
        flaky_calls.append(urls)
        if len(flaky_calls) == 1:
            raise ConnectionResetError("connection dropped")
        return {"added": 1, "events": [{"id": urls[0]}]}

    q = JobQueue(flaky, backoff_base_s=0)
    healed = q.submit(["https://x.test/1"], "g1")
    await q.drain()
    assert healed.state == "done" and healed.attempts == 2
    assert healed.last_error == "ConnectionResetError: connection dropped"

    bug_calls = []

    async def bug(urls, guild_id, on_stage):
        bug_calls.append(urls)
        raise ValueError("venue field missing")

    q2 = JobQueue(bug, backoff_base_s=0)
    broken = q2.submit(["https://x.test/2"], "g1")
    await q2.drain()
    assert broken.state == "failed" and broken.attempts == 1 and len(bug_calls) == 1
    assert broken.error == "ValueError: venue field missing"


@pytest.mark.asyncio
async def test_a_retry_that_blows_up_keeps_the_spots_already_saved():
    calls = []

    async def partial_then_down(urls, guild_id, on_stage):
        calls.append(list(urls))
        if len(calls) == 1:
            return {"added": 1, "rejected": 0, "unreadable": 1,
                    "retryable_urls": ["https://x.test/2"],
                    "events": [{"id": "https://x.test/1"}], "log": []}
        raise ValueError("pipeline broke on retry")

    q = JobQueue(partial_then_down, backoff_base_s=0)
    job = q.submit(["https://x.test/1", "https://x.test/2"], "g1")
    await q.drain()
    assert job.state == "done"                         # link 1's spot is real and stays
    assert job.result["added"] == 1 and job.result["unreadable"] == 1
    assert job.last_error == "ValueError: pipeline broke on retry"


@pytest.mark.asyncio
async def test_failed_jobs_are_listed_per_server_newest_first(tmp_path):
    async def boom(urls, guild_id, on_stage):
        raise ValueError(f"nope {urls[0]}")

    store = SqliteJobStore(tmp_path / "jobs.db")
    q = JobQueue(boom, store=store, backoff_base_s=0)
    first = q.submit(["https://x.test/1"], "g1")
    await q.drain()
    second = q.submit(["https://x.test/2"], "g1")
    other = q.submit(["https://x.test/3"], "g2")
    await q.drain()

    listed = q.failed("g1")
    assert [j.id for j in listed] == [second.id, first.id]
    assert other.id not in {j.id for j in listed}      # another server's failures stay theirs
    assert listed[0].attempts == 1 and "nope" in listed[0].error
    store.close()


def test_an_old_job_database_gains_the_new_columns(tmp_path):
    import sqlite3

    db = tmp_path / "jobs.db"
    old = sqlite3.connect(db)                            # the table as it shipped in #24
    old.executescript("""
        CREATE TABLE jobs (id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, urls TEXT NOT NULL,
          state TEXT NOT NULL, stage TEXT NOT NULL, done_urls INTEGER NOT NULL DEFAULT 0,
          result TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
          created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL);
        INSERT INTO jobs VALUES ('old1','g1','["https://x.test/1"]','running','fetching',
          0,NULL,NULL,1,1.0,1.0,NULL);
    """)
    old.commit(); old.close()

    store = SqliteJobStore(db)
    job = store.load("old1")
    assert job.recoveries == 0 and job.last_error is None
    q = JobQueue(fake_run, store=store)
    assert q.recover() == {"requeued": 1, "failed": 0}  # an old row still recovers
    store.close()


def test_failed_jobs_endpoint_is_tenant_scoped(monkeypatch):
    import src.ingestion.serving.admin as admin
    from src.ingestion.serving.tenant_auth import ENV_VAR, SCOPE_READ, mint_token

    monkeypatch.setenv(ENV_VAR, "0" * 64)

    async def boom(urls, guild_id, on_stage):
        raise ValueError("venue field missing")

    monkeypatch.setattr(admin, "_jobs", JobQueue(boom, backoff_base_s=0))
    mine = {"X-Tenant-Token": mint_token("g1")}
    with TestClient(admin.app) as client:
        job_id = client.post("/api/jobs", json={"urls": ["https://x.test/1"], "guild_id": "g1"},
                             headers=mine).json()["job_id"]
        _wait_done(client, job_id, headers=mine)

        listed = client.get("/api/jobs?guild_id=g1&state=failed", headers=mine)
        assert listed.status_code == 200
        [row] = listed.json()["jobs"]
        assert row["id"] == job_id and row["attempts"] == 1
        assert row["urls"] == ["https://x.test/1"]

        share = {"X-Tenant-Token": mint_token("g1", scope=SCOPE_READ)}
        assert client.get("/api/jobs?guild_id=g1", headers=share).status_code == 200
        assert client.get("/api/jobs?guild_id=g1").status_code == 403
        theirs = {"X-Tenant-Token": mint_token("g2")}
        assert client.get("/api/jobs?guild_id=g1", headers=theirs).status_code == 403
        assert client.get("/api/jobs?guild_id=g1&state=done", headers=mine).status_code == 400


# ── HTTP endpoints ──────────────────────────────────────────────────────────


def _wait_done(client, job_id, tries=100, headers=None):
    for _ in range(tries):
        job = client.get(f"/api/jobs/{job_id}", headers=headers or {}).json()
        if job["state"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    return job


def test_job_endpoints_enqueue_poll_and_finish(monkeypatch):
    import src.ingestion.serving.admin as admin
    from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token

    monkeypatch.setenv(ENV_VAR, "0" * 64)
    auth = {"X-Tenant-Token": mint_token("g1")}      # what the bot sends for guild g1
    monkeypatch.setattr(admin, "_jobs", JobQueue(fake_run))
    # The context manager keeps one event loop alive across requests, which is
    # what the worker task needs (uvicorn has one loop; TestClient without the
    # `with` would spin a fresh one per request).
    with TestClient(admin.app) as client:
        resp = client.post(
            "/api/jobs", json={"urls": ["https://x.test/1"], "guild_id": "g1"}, headers=auth
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        job = _wait_done(client, job_id, headers=auth)
        assert job["state"] == "done"
        assert job["stage"] == "done"
        assert job["progress"] == {"done": 1, "total": 1}
        assert job["result"]["events"][0]["id"] == "https://x.test/1"
        assert job["guild_id"] == "g1"

        assert client.get("/api/jobs/does-not-exist").status_code == 404


def test_job_endpoint_validates_like_ingest(monkeypatch):
    import src.ingestion.serving.admin as admin

    monkeypatch.setattr(admin, "_jobs", JobQueue(fake_run))
    with TestClient(admin.app) as client:
        assert client.post("/api/jobs", json={"urls": ["ftp://nope.example/x"]}).status_code == 400
        assert client.post("/api/jobs", json={"urls": [f"https://x.test/{i}" for i in range(11)]}).status_code == 400
        assert client.post("/api/jobs", json={"urls": ["   "]}).status_code == 400


def test_job_endpoint_returns_429_when_the_queue_is_full(monkeypatch):
    import src.ingestion.serving.admin as admin

    async def slow(urls, guild_id, on_stage):
        await asyncio.sleep(0.4)
        return {"added": 0, "events": []}

    monkeypatch.setattr(admin, "_jobs", JobQueue(slow, max_queued=1))
    with TestClient(admin.app) as client:
        # Distinct links: the same link twice would join the first capture
        # instead of queueing a second one (feature #25).
        first = client.post("/api/jobs", json={"urls": ["https://x.test/1"]})   # worker takes it
        second = client.post("/api/jobs", json={"urls": ["https://x.test/2"]})  # waits in the queue
        third = client.post("/api/jobs", json={"urls": ["https://x.test/3"]})   # over the cap
        assert (first.status_code, second.status_code, third.status_code) == (202, 202, 429)
        assert "queue is full" in third.json()["detail"]
        _wait_done(client, second.json()["job_id"])
