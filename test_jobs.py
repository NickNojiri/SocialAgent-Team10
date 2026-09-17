"""Offline tests for the async capture job queue (ADR-0004) — no browser, no
network: the capture function is faked, the endpoints run under TestClient.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from src.ingestion.serving.jobs import JobQueue, QueueFull

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
