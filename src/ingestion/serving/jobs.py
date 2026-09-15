"""In-process job queue for reel capture (ADR-0004).

The bot enqueues a capture with `POST /api/jobs`, gets a job id back at once, and
polls `GET /api/jobs/{id}` while the admin app works through the queue. No new
infrastructure: one asyncio worker task per configured slot, an in-memory store,
finished jobs swept after a TTL. A restart loses in-flight jobs — the bot sees a
404 on its next poll and shows the Retry view; accepted for the self-host.

`JobQueue` is deliberately small and behind a plain interface (`submit`, `get`)
so a SQLite-backed store can replace it without touching the endpoints.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

log = logging.getLogger("ingestion.jobs")

# Stage names the pipeline reports through `IngestionPipeline(on_stage=...)`, in
# the order a single URL moves through them. The bot maps these to status text.
STAGES = ("queued", "fetching", "transcribing", "extracting", "saving", "done")

# run(urls, guild_id, on_stage) -> the same dict POST /api/ingest returns.
RunFn = Callable[[list[str], str, Callable[[str, str], None]], Awaitable[dict]]


class QueueFull(Exception):
    """More jobs are waiting than `max_queued` allows."""


@dataclass
class Job:
    id: str
    guild_id: str
    urls: list[str]
    state: str = "queued"           # queued | running | done | failed
    stage: str = "queued"           # last stage the pipeline reported
    done_urls: int = 0              # URLs that reached "done"
    result: Optional[dict] = None   # populated when state == "done"
    error: Optional[str] = None     # populated when state == "failed"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def touch(self, stage: Optional[str] = None) -> None:
        if stage:
            self.stage = stage
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "guild_id": self.guild_id,
            "state": self.state,
            "stage": self.stage,
            "progress": {"done": self.done_urls, "total": len(self.urls)},
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobQueue:
    def __init__(
        self,
        run: RunFn,
        *,
        workers: int = 1,
        max_queued: int = 50,
        ttl_s: float = 3600.0,
    ):
        self._run = run
        self.workers = max(1, int(workers))
        self.max_queued = max_queued
        self.ttl_s = ttl_s
        self._jobs: dict[str, Job] = {}
        # Created lazily on the running loop: the module is imported before any
        # loop exists (uvicorn, TestClient), and asyncio primitives bind to one.
        self._queue: Optional[asyncio.Queue] = None
        self._tasks: list[asyncio.Task] = []

    # ── public ────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def submit(self, urls: list[str], guild_id: str = "") -> Job:
        """Queue a capture. Must be called from within the event loop."""
        self._ensure_workers()
        self.sweep()
        waiting = sum(1 for j in self._jobs.values() if j.state == "queued")
        if waiting >= self.max_queued:
            raise QueueFull(f"{waiting} captures already waiting")
        job = Job(id=uuid.uuid4().hex[:12], guild_id=str(guild_id or ""), urls=list(urls))
        self._jobs[job.id] = job
        self._queue.put_nowait(job.id)
        log.info("[jobs] queued %s (%d url(s), guild=%r)", job.id, len(job.urls), job.guild_id)
        return job

    def sweep(self, now: Optional[float] = None) -> int:
        """Drop finished jobs older than the TTL. Returns how many were removed."""
        now = time.time() if now is None else now
        stale = [
            j.id for j in self._jobs.values()
            if j.finished_at is not None and now - j.finished_at > self.ttl_s
        ]
        for job_id in stale:
            del self._jobs[job_id]
        return len(stale)

    async def drain(self) -> None:
        """Wait until every queued job has been processed (tests, shutdown)."""
        if self._queue is not None:
            await self._queue.join()

    @property
    def pending(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state in ("queued", "running"))

    # ── internals ─────────────────────────────────────────────────────────

    def _ensure_workers(self) -> None:
        loop = asyncio.get_running_loop()
        if self._queue is None:
            self._queue = asyncio.Queue()
        alive = [t for t in self._tasks if not t.done()]
        while len(alive) < self.workers:
            alive.append(loop.create_task(self._worker(), name=f"ingest-worker-{len(alive)}"))
        self._tasks = alive

    async def _worker(self) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None:                  # swept while waiting — nothing to do
                self._queue.task_done()
                continue
            job.state = "running"
            job.touch("fetching")

            def on_stage(url: str, stage: str, _job: Job = job) -> None:
                # Called from the pipeline's worker thread; plain attribute
                # assignment is safe, and that is all this does.
                _job.touch(stage)
                if stage == "done":
                    _job.done_urls += 1

            try:
                job.result = await self._run(job.urls, job.guild_id, on_stage)
                job.state = "done"
                job.done_urls = len(job.urls)
                job.touch("done")
            except Exception as exc:          # one bad job never stops the worker
                log.exception("[jobs] %s failed: %s", job.id, exc)
                job.state = "failed"
                job.error = f"{type(exc).__name__}: {exc}"
                job.touch()
            finally:
                job.finished_at = time.time()
                self._queue.task_done()
