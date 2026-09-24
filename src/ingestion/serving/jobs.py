"""In-process job queue for reel capture (ADR-0004).

The bot enqueues a capture with `POST /api/jobs`, gets a job id back at once, and
polls `GET /api/jobs/{id}` while the admin app works through the queue. No new
infrastructure: one asyncio worker task per configured slot, an in-memory store,
finished jobs swept after a TTL. A restart loses in-flight jobs â€” the bot sees a
404 on its next poll and shows the Retry view; accepted for the self-host.

`JobQueue` is deliberately small and behind a plain interface (`submit`, `get`)
so a SQLite-backed store can replace it without touching the endpoints.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger("ingestion.jobs")

# Tracking parameters social platforms append to a shared link. Two people
# pasting the same reel rarely paste the same string, so they are dropped
# before a link is compared with another (feature #23 duplicate counting,
# feature #25 idempotency).
_TRACKING_PARAMS = ("igsh", "igshid", "utm_", "fbclid", "si", "_r", "_t", "is_from_webapp")


def normalize_capture_url(url: str) -> str:
    """Canonical form of a reel link: no tracking params, no trailing slash.

    Conservative on purpose â€” it only lowercases the host and drops known
    tracking parameters, so two links that normalize alike really are the same
    post. Anything it can't parse comes back stripped but otherwise untouched.
    """
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    if not parts.scheme or not parts.netloc:
        return raw
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = "&".join(
        piece for piece in parts.query.split("&")
        if piece and not any(piece.lower().startswith(p) for p in _TRACKING_PARAMS)
    )
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def capture_key(guild_id: str, url: str) -> str:
    """Identity of "this server capturing this post" â€” the dedup key."""
    material = f"{str(guild_id or '')}\n{normalize_capture_url(url)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

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
    attempts: int = 0               # runs started, including recovery after a restart
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    # Seconds spent in each stage, summed over the job's URLs (feature #23).
    stage_times: dict[str, float] = field(default_factory=dict)
    _stage_since: float = field(default_factory=time.time, repr=False)

    def touch(self, stage: Optional[str] = None) -> None:
        """Record progress, banking the time since the last touch.

        Every touch credits the elapsed time to the stage the job was in, so
        the clock is never lost between two stage changes. Stages repeat across
        a multi-URL job, so the entries accumulate rather than overwrite.
        """
        now = time.time()
        self.stage_times[self.stage] = self.stage_times.get(self.stage, 0.0) + (
            now - self._stage_since
        )
        self._stage_since = now
        if stage:
            self.stage = stage
        self.updated_at = now

    @property
    def duration_s(self) -> float:
        return (self.finished_at or time.time()) - self.created_at

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
            "timing": {
                "duration_s": round(self.duration_s, 3),
                "stages": {k: round(v, 3) for k, v in self.stage_times.items()},
            },
        }

    def url_keys(self) -> list[str]:
        """This job's links as dedup keys (feature #25)."""
        return [capture_key(self.guild_id, u) for u in self.urls]

    def log_row(self) -> dict:
        """One line for the capture log â€” what `scripts/summarize_captures.py` reads.

        Carries no token, no user id and no message text: the links are the
        same ones already stored in the catalog, plus their dedup keys.
        """
        return {
            "id": self.id,
            "guild_id": self.guild_id,
            "state": self.state,
            "urls": len(self.urls),
            "url_keys": [capture_key(self.guild_id, u) for u in self.urls],
            "duration_s": round(self.duration_s, 3),
            "stages": {k: round(v, 3) for k, v in self.stage_times.items()},
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobStore:
    """Where jobs live between a submit and a poll.

    The default keeps them in memory (ADR-0004): a restart loses in-flight
    work. `SqliteJobStore` is the durable alternative (ADR-0005) â€” same three
    methods, chosen by configuration.
    """

    def save(self, job: Job) -> None:            # pragma: no cover - interface
        raise NotImplementedError

    def load(self, job_id: str) -> Optional[Job]:  # pragma: no cover - interface
        raise NotImplementedError

    def drop(self, job_id: str) -> None:         # pragma: no cover - interface
        raise NotImplementedError

    def unfinished(self) -> list[Job]:
        """Jobs left queued or running by a previous process. Empty if volatile."""
        return []


class MemoryJobStore(JobStore):
    """The historical behaviour: nothing outlives the process."""

    def save(self, job: Job) -> None:
        return None

    def load(self, job_id: str) -> Optional[Job]:
        return None

    def drop(self, job_id: str) -> None:
        return None


class SqliteJobStore(JobStore):
    """One SQLite file, one table. No server, no new dependency (stdlib).

    Writes happen at state changes (queued â†’ running â†’ done/failed), not on
    every stage tick: the bot polls the live in-memory job, so the database
    only has to be good enough to explain what a restart interrupted.
    """

    _DDL = """
    CREATE TABLE IF NOT EXISTS jobs (
        id          TEXT PRIMARY KEY,
        guild_id    TEXT NOT NULL,
        urls        TEXT NOT NULL,
        state       TEXT NOT NULL,
        stage       TEXT NOT NULL,
        done_urls   INTEGER NOT NULL DEFAULT 0,
        result      TEXT,
        error       TEXT,
        attempts    INTEGER NOT NULL DEFAULT 0,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        finished_at REAL
    );
    CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # The worker coroutine and the polling request are different threads
        # under uvicorn; one connection guarded by a lock keeps writes ordered.
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(self._DDL)
            self._db.commit()

    def save(self, job: Job) -> None:
        row = (
            job.id, job.guild_id, json.dumps(job.urls), job.state, job.stage,
            job.done_urls,
            json.dumps(job.result) if job.result is not None else None,
            job.error, job.attempts, job.created_at, job.updated_at, job.finished_at,
        )
        with self._lock:
            self._db.execute(
                "INSERT INTO jobs (id, guild_id, urls, state, stage, done_urls, result,"
                " error, attempts, created_at, updated_at, finished_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET state=excluded.state, stage=excluded.stage,"
                " done_urls=excluded.done_urls, result=excluded.result, error=excluded.error,"
                " attempts=excluded.attempts, updated_at=excluded.updated_at,"
                " finished_at=excluded.finished_at",
                row,
            )
            self._db.commit()

    def load(self, job_id: str) -> Optional[Job]:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_job(row) if row else None

    def drop(self, job_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._db.commit()

    def unfinished(self) -> list[Job]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs WHERE state IN ('queued','running') ORDER BY created_at"
            ).fetchall()
        return [self._to_job(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            guild_id=row["guild_id"],
            urls=json.loads(row["urls"]),
            state=row["state"],
            stage=row["stage"],
            done_urls=row["done_urls"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            attempts=row["attempts"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            finished_at=row["finished_at"],
        )


class JobQueue:
    def __init__(
        self,
        run: RunFn,
        *,
        workers: int = 1,
        max_queued: int = 50,
        ttl_s: float = 3600.0,
        log_path: Optional[Path] = None,
        store: Optional[JobStore] = None,
    ):
        self._run = run
        self.workers = max(1, int(workers))
        self.max_queued = max_queued
        self.ttl_s = ttl_s
        # Finished jobs are swept after the TTL, so their timings are appended
        # here first; None (the default, and what tests use) writes nothing.
        self.log_path = Path(log_path) if log_path else None
        self.store: JobStore = store or MemoryJobStore()
        self._jobs: dict[str, Job] = {}
        # Created lazily on the running loop: the module is imported before any
        # loop exists (uvicorn, TestClient), and asyncio primitives bind to one.
        self._queue: Optional[asyncio.Queue] = None
        self._tasks: list[asyncio.Task] = []
        # Jobs recovered from the store before a loop existed; enqueued as soon
        # as the workers start.
        self._pending_recovered: list[str] = []

    # â”€â”€ public â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def get(self, job_id: str) -> Optional[Job]:
        """The live job, or the durable copy left by a previous process."""
        job = self._jobs.get(job_id)
        if job is None:
            job = self.store.load(job_id)
        return job

    def find_active(self, guild_id: str, urls: list[str]) -> Optional[Job]:
        """The job already capturing all of these links for this server, if any.

        This is what makes a double paste — or the Retry button — return the
        capture that is already running instead of starting a second one
        (feature #25). Only queued and running jobs count: once a capture has
        finished, pasting the link again is a deliberate re-capture.
        """
        wanted = {capture_key(guild_id, u) for u in urls}
        if not wanted:
            return None
        for job in self._jobs.values():
            if job.state in ("queued", "running") and wanted <= set(job.url_keys()):
                return job
        return None

    def submit(self, urls: list[str], guild_id: str = "") -> Job:
        """Queue a capture. Must be called from within the event loop.

        Idempotent: the same links, from the same server, while a capture of
        them is still in flight, return that capture. Links that are already
        in flight are dropped from a partly-new request, so a link is never
        captured twice at once.
        """
        self._ensure_workers()
        self.sweep()
        guild_id = str(guild_id or "")

        existing = self.find_active(guild_id, urls)
        if existing is not None:
            log.info("[jobs] %s already capturing those link(s) — reusing it", existing.id)
            return existing
        in_flight = {
            k for j in self._jobs.values() if j.state in ("queued", "running")
            for k in j.url_keys()
        }
        fresh = [u for u in urls if capture_key(guild_id, u) not in in_flight]
        if not fresh:                       # every link is already being captured
            covering = self.find_active(guild_id, urls[:1])
            if covering is not None:
                return covering
            fresh = list(urls)
        if len(fresh) < len(urls):
            log.info("[jobs] %d of %d link(s) are already in flight — capturing the rest",
                     len(urls) - len(fresh), len(urls))
        urls = fresh

        waiting = sum(1 for j in self._jobs.values() if j.state == "queued")
        if waiting >= self.max_queued:
            raise QueueFull(f"{waiting} captures already waiting")
        job = Job(id=uuid.uuid4().hex[:12], guild_id=guild_id, urls=list(urls))
        self._jobs[job.id] = job
        self._save(job)
        self._queue.put_nowait(job.id)
        log.info("[jobs] queued %s (%d url(s), guild=%r)", job.id, len(job.urls), job.guild_id)
        return job

    def recover(self, max_attempts: int = 1) -> dict[str, int]:
        """Deal with whatever the last process left behind (ADR-0005).

        A job that was queued or running gets one more attempt; one that has
        already used it is failed with a reason, so a capture never sits in
        limbo and the bot never polls a job that will never move again.
        Returns {"requeued": n, "failed": n}.
        """
        counts = {"requeued": 0, "failed": 0}
        for job in self.store.unfinished():
            if job.attempts > max_attempts:
                job.state = "failed"
                job.error = "lost when the service restarted, after one retry"
                job.finished_at = time.time()
                job.touch()
                self._save(job)
                counts["failed"] += 1
                continue
            job.state = "queued"
            job.stage = "queued"
            job.touch()
            self._jobs[job.id] = job
            self._save(job)
            self._pending_recovered.append(job.id)
            counts["requeued"] += 1
        if counts["requeued"] or counts["failed"]:
            log.info("[jobs] recovered %d, failed %d after restart",
                     counts["requeued"], counts["failed"])
        return counts

    def sweep(self, now: Optional[float] = None) -> int:
        """Drop finished jobs older than the TTL. Returns how many were removed."""
        now = time.time() if now is None else now
        stale = [
            j.id for j in self._jobs.values()
            if j.finished_at is not None and now - j.finished_at > self.ttl_s
        ]
        for job_id in stale:
            del self._jobs[job_id]
            self.store.drop(job_id)
        return len(stale)

    async def drain(self) -> None:
        """Wait until every queued job has been processed (tests, shutdown)."""
        if self._queue is not None:
            await self._queue.join()

    def start(self) -> None:
        """Start the workers (and pick up anything `recover()` requeued).

        Must be called from inside the event loop — the service does it on
        startup, right after `recover()`.
        """
        self._ensure_workers()

    def shutdown(self) -> None:
        """Stop the workers without waiting â€” what a restart looks like.

        Whatever was running stays `running` in the store; `recover()` in the
        next process decides what happens to it.
        """
        for task in self._tasks:
            task.cancel()
        self._tasks = []

    @property
    def pending(self) -> int:
        return sum(1 for j in self._jobs.values() if j.state in ("queued", "running"))

    # â”€â”€ internals â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _ensure_workers(self) -> None:
        loop = asyncio.get_running_loop()
        if self._queue is None:
            self._queue = asyncio.Queue()
        alive = [t for t in self._tasks if not t.done()]
        while len(alive) < self.workers:
            alive.append(loop.create_task(self._worker(), name=f"ingest-worker-{len(alive)}"))
        self._tasks = alive
        while self._pending_recovered:              # requeued by recover()
            self._queue.put_nowait(self._pending_recovered.pop(0))

    async def _worker(self) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            job = self._jobs.get(job_id)
            if job is None:                  # swept while waiting â€” nothing to do
                self._queue.task_done()
                continue
            job.state = "running"
            job.attempts += 1
            job.touch("fetching")
            self._save(job)

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
                job.touch()                   # bank the last stage's time
                self._save(job)
                self._append_log(job)
                self._queue.task_done()

    def _save(self, job: Job) -> None:
        """Persist a state change. A store that is gone must not kill a capture."""
        try:
            self.store.save(job)
        except Exception as exc:                  # a closed/locked database, a full disk
            log.warning("[jobs] could not persist %s: %s", job.id, exc)

    def _append_log(self, job: Job) -> None:
        """Append one JSON line per finished job. Never fails the job."""
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(job.log_row(), ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("[jobs] could not write the capture log: %s", exc)
