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
    QueueFull,
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
        body = {"urls": ["https://x.test/1"]}
        first = client.post("/api/jobs", json=body)      # picked up by the worker
        second = client.post("/api/jobs", json=body)     # waits in the queue
        third = client.post("/api/jobs", json=body)      # over the cap
        assert (first.status_code, second.status_code, third.status_code) == (202, 202, 429)
        assert "queue is full" in third.json()["detail"]
        _wait_done(client, second.json()["job_id"])
