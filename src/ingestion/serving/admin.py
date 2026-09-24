"""Admin web UI for the event catalog — add / view / remove / vote.

Runs on the HOST (needs the full ingestion deps: Playwright, LLM, etc.) and
reads/writes the SAME Chroma store the bot + recommend service use, so anything
you add or remove here shows up to the Discord bot immediately.

    uvicorn src.ingestion.serving.admin:app --port 8010
    # then open http://localhost:8010

Votes are stored in each event's Chroma metadata (`votes`), so the recommender
can later rank by popularity.
"""

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.ingestion.cli import (
    build_extractor,
    build_temporal_resolver,
)
from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.orchestrator import IngestionPipeline, result_line
from src.ingestion.pipeline.summarizer import summarize_place
from src.ingestion.serving import capture_stats
from src.ingestion.serving import forget as data_purge
from src.ingestion.serving.capture_limits import (
    CaptureRateLimitExceeded,
    CaptureRateLimiter,
)
from src.ingestion.serving.failures import failures
from src.ingestion.serving.feedback import FeedbackError, FeedbackStore
from src.ingestion.serving.guild_settings import GuildSettingsStore, SettingsError, clean_city
from src.ingestion.serving.time_to_card import TimeToCardLog, TimingError
from src.ingestion.serving.jobs import (
    JobQueue,
    MemoryJobStore,
    QueueFull,
    SqliteJobStore,
    retryable_urls,
)
from src.ingestion.serving.tenant_auth import SCOPE_READ, authorize
from src.ingestion.sinks.chroma_sink import ChromaSink, collection_for_guild
from src.ingestion.sinks.jsonl_sink import JsonlSink

log = logging.getLogger("ingestion.admin")

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """On startup, deal with whatever a previous process left running (ADR-0005),
    and finish any server deletion (#21) that couldn't remove its index files."""
    finished = data_purge.finish_pending(Path(_settings.chroma_path))
    if finished["removed"] or finished["left"]:
        print(f"[forget] finished earlier deletions: removed {finished['removed']} index "
              f"folder(s), {finished['left']} still in use")
    counts = _jobs.recover()
    _jobs.start()
    if counts["requeued"] or counts["failed"]:
        print(f"[jobs] after restart: requeued {counts['requeued']}, failed {counts['failed']}")
    yield
    _jobs.shutdown()


app = FastAPI(title="SocialAgent Admin", lifespan=_lifespan)

# Speed/quality knobs, tunable from .env without code changes. Capture time is
# dominated by three stages: page render (SETTLE_TIMEOUT_MS), Whisper on CPU
# (WHISPER_MODEL / TRANSCRIBE_ENABLED), and Ollama inference (OLLAMA_MODEL —
# e.g. llama3.2:3b is ~2-3x faster than llama3.1:8b at some quality cost).
_env_overrides: dict = {}
for _env, _field, _cast in (
    ("OLLAMA_URL", "ollama_url", str),
    ("OLLAMA_MODEL", "ollama_model", str),
    ("EMBED_MODEL", "embed_model", str),
    ("WHISPER_MODEL", "whisper_model", str),
    ("SETTLE_TIMEOUT_MS", "settle_timeout_ms", int),
    ("CAPTURE_BUDGET_S", "capture_budget_s", float),
):
    _val = os.getenv(_env, "").strip()
    if _val:
        _env_overrides[_field] = _cast(_val)

_settings = IngestionSettings(chroma_enabled=True, geocode_enabled=False, **_env_overrides)
# Each server's captures and failed-page snapshots go in files of its own, so
# deleting its data (#21) is deleting them. The legacy "" catalog keeps the
# original shared paths.
_JSONL_DIR = Path("data/inspirations")
_LEGACY_JSONL = Path("data/inspirations.jsonl")
_RAW_ROOT = Path(_settings.raw_dir)
_TRANSCRIBE_OFF = os.getenv("TRANSCRIBE_ENABLED", "").strip().lower() in ("0", "false", "off", "no")

# One catalog per Discord guild (or DM stash). "" is the legacy/single-tenant
# collection, so existing data and the web UI keep working unchanged.
_sinks: dict[str, ChromaSink] = {}


def _sink_for(guild_id: str = "", *, create: bool = True) -> ChromaSink:
    """A server's catalog. Read-only endpoints pass create=False: a server with
    no catalog then reads as empty instead of getting one made — so a server
    whose data was deleted (#21) doesn't reappear because someone looked."""
    key = str(guild_id or "")
    if key not in _sinks:
        if not create and key and not _catalog_exists(key):
            return _EMPTY_CATALOG
        _sinks[key] = ChromaSink(
            _settings, collection_name=collection_for_guild(_settings, key)
        )
    return _sinks[key]


def _catalog_exists(guild_id: str) -> bool:
    name = collection_for_guild(_settings, guild_id)
    return any(getattr(c, "name", c) == name for c in _sink_for("").client.list_collections())


class _EmptyCollection:
    def get(self, ids=None, include=None):
        return {"ids": [], "metadatas": [], "documents": []}

    def count(self):
        return 0

    def delete(self, ids=None):
        return None


class _EmptyCatalog:
    collection = _EmptyCollection()


_EMPTY_CATALOG = _EmptyCatalog()

# Route host TLS through the Windows cert store so the reel video download works on
# TLS-intercepting networks (see the transcriber). Best-effort.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass


def _build_transcriber():
    """Whisper transcriber for reel audio, built once (model loads on first use).
    Stays None when faster-whisper isn't installed — or when TRANSCRIBE_ENABLED=0
    (the biggest single speed win on CPU-only machines) — so ingest skips audio."""
    if _TRANSCRIBE_OFF:
        import logging

        logging.getLogger("ingestion.admin").info("[stt] transcription disabled via TRANSCRIBE_ENABLED=0")
        return None
    try:
        from faster_whisper import WhisperModel

        from src.ingestion.pipeline.transcriber import Transcriber

        model = WhisperModel(_settings.whisper_model, device="cpu", compute_type="int8")

        def _fn(path: str):
            segments, _info = model.transcribe(path, beam_size=1, vad_filter=True)
            return " ".join(s.text for s in segments).strip()

        return Transcriber(_settings, transcribe_fn=_fn)
    except Exception as exc:
        import logging

        logging.getLogger("ingestion.admin").warning(f"[stt] transcription off: {exc}")
        return None


_transcriber = _build_transcriber()


def _build_ig_source():
    """Authenticated IG fetch source, built once when IG_USERNAME/IG_PASSWORD are
    set (and instagrapi is installed). None → the pipeline uses the Playwright
    path exactly as before. Login is lazy (on first capture)."""
    if not (_settings.ig_username and _settings.ig_password):
        return None
    try:
        from src.ingestion.sources.ig_authed import AuthedInstagramSource

        import logging

        logging.getLogger("ingestion.admin").info(
            "[ig] authenticated fetch source enabled (user=%s)", _settings.ig_username
        )
        return AuthedInstagramSource(_settings)
    except Exception as exc:
        import logging

        logging.getLogger("ingestion.admin").warning(f"[ig] authed source off: {exc}")
        return None


_ig_source = _build_ig_source()


def _build_ocr():
    """Cover-image OCR (on-screen text), opt-in via OCR_ENABLED=1. The reader
    degrades to no-text when rapidocr isn't installed, so this never fails."""
    if os.getenv("OCR_ENABLED", "").strip().lower() not in ("1", "true", "on", "yes"):
        return None
    from src.ingestion.pipeline.ocr import ImageTextReader

    return ImageTextReader(_settings)


_ocr_reader = _build_ocr()

# /api/ingest input guardrails — reject garbage instantly instead of feeding it
# a 30s browser cycle.
_MAX_URLS_PER_INGEST = 10

# Rolling behind-the-scenes feed for the /dash page (in-memory, newest first).
from collections import deque

_CAPTURE_LOG: deque = deque(maxlen=50)

# guild id → captures running in _run_ingest right now (sync or queued path).
_capturing: dict[str, int] = {}


class IngestBody(BaseModel):
    urls: list[str]
    guild_id: str = ""      # "" → the legacy/single-tenant catalog
    user_id: str = ""       # signed into the token for per-user capture limits


class VoteBody(BaseModel):
    delta: int = 1
    # When set, votes carry identity: "Want to go" joins the voters list,
    # "Not for me" leaves it, and the count is the list's size. Anonymous
    # votes (the web UI) keep the plain counter behavior.
    #
    # user_id is claimed by the caller, so on a real (non-empty) tenant the
    # tenant token must be signed over this exact value — see tenant_auth.
    # Otherwise anyone could vote, or un-vote, as anyone else.
    user_id: str = ""
    user_name: str = ""


def _voters(meta: dict) -> dict:
    """The {user_id: display_name} map stored as a JSON string in metadata."""
    try:
        return json.loads(meta.get("voters") or "{}")
    except (TypeError, ValueError):
        return {}


def _apply_vote(meta: dict, body: VoteBody) -> dict:
    """Pure vote transition — split out so the quorum logic is testable offline."""
    if body.user_id:
        voters = _voters(meta)
        if body.delta > 0:
            voters[body.user_id] = body.user_name or body.user_id
        else:
            voters.pop(body.user_id, None)
        meta["voters"] = json.dumps(voters)
        meta["votes"] = len(voters)
    else:
        meta["votes"] = int(meta.get("votes", 0)) + body.delta
    return meta


# ── API ──────────────────────────────────────────────────────────────────────


# ── Tenant authorization ─────────────────────────────────────────────────────
# Every endpoint that names a guild_id checks an X-Tenant-Token signed with
# SPOTBOT_SIGNING_KEY (serving/tenant_auth.py). The bot is the only minter.
# The empty tenant "" (legacy single-tenant catalog, the local web UI) stays
# open by design — bind to 127.0.0.1. /api/stats and /dash are operator views
# (counts per guild, no spot contents) and are likewise localhost-only.
# docs/THREAT_MODEL.md T2.

TenantToken = Header(default=None, alias="X-Tenant-Token")


@app.get("/api/events")
def list_events(guild_id: str = "", x_tenant_token: str | None = TenantToken):
    authorize(guild_id, x_tenant_token, need=SCOPE_READ)
    res = _sink_for(guild_id, create=False).collection.get(include=["metadatas"])
    events = []
    for event_id, m in zip(res["ids"], res["metadatas"]):
        m = m or {}
        events.append(
            {
                "id": event_id,
                "venue": m.get("venue_name", "Unknown"),
                "category": m.get("category", "other"),
                "theme": m.get("core_theme", ""),
                "source_url": m.get("source_url", ""),
                "image": m.get("image_url", ""),
                "lat": m.get("lat"),
                "lng": m.get("lng"),
                "blurb": m.get("summary", ""),
                "votes": int(m.get("votes", 0)),
                "voters": sorted(_voters(m).values()),
                "schedule": m.get("schedule_status", "unscheduled"),
                "start_utc": m.get("start_utc", ""),
                # /browse filters (#35): where the post said it is, and whether
                # the spot has a date — from the reel, or locked in by the group.
                "area": m.get("raw_location_text", ""),
                "start_epoch": m.get("start_epoch"),
                "locked_end_epoch": m.get("locked_end_epoch"),
            }
        )
    events.sort(key=lambda e: (-e["votes"], e["venue"]))
    return {"events": events, "count": len(events)}


def _ingest_urls(body: IngestBody) -> list[str]:
    """Validate a capture request. Garbage gets a 400 in milliseconds, never a
    browser cycle — shared by the sync endpoint and the job queue."""
    urls = [u.strip() for u in body.urls if u.strip()]
    if not urls:
        raise HTTPException(400, "no urls given")
    if len(urls) > _MAX_URLS_PER_INGEST:
        raise HTTPException(400, f"too many links — max {_MAX_URLS_PER_INGEST} per request")
    bad = [u for u in urls if not u.lower().startswith(("http://", "https://"))]
    if bad:
        raise HTTPException(400, f"only http(s) URLs are ingestible, got: {bad[0][:80]!r}")
    return urls


async def _run_ingest(urls: list[str], guild_id: str = "", on_stage=None) -> dict:
    """One capture run — the sync endpoint awaits it directly, the job queue
    runs it in a worker with `on_stage` feeding the job's progress."""
    sink = _sink_for(guild_id)
    existing_ids = set(sink.collection.get(include=[])["ids"])  # snapshot before the run
    key = data_purge.guild_file_key(guild_id)
    pipeline = IngestionPipeline(
        _settings.model_copy(update={"raw_dir": _RAW_ROOT / key}) if key else _settings,
        extractor=build_extractor(_settings),
        transcriber=_transcriber,
        summarizer=lambda cap, tr: summarize_place(cap, tr, _settings),
        geo_enricher=None,  # geocoding off here (fast; Nominatim often blocked)
        temporal_resolver=build_temporal_resolver(_settings),
        jsonl_sink=JsonlSink(_JSONL_DIR / f"{key}.jsonl" if key else _LEGACY_JSONL),
        chroma_sink=sink,
        authed_source=_ig_source,
        ocr_reader=_ocr_reader,
        on_stage=on_stage,
    )
    # Counted so a deletion (#21) can refuse while this server is mid-capture.
    _capturing[guild_id] = _capturing.get(guild_id, 0) + 1
    try:
        report = await pipeline.run(urls)
    finally:
        _capturing[guild_id] -= 1
        if not _capturing[guild_id]:
            del _capturing[guild_id]
    _CAPTURE_LOG.appendleft(
        {
            "ts": int(time.time()),
            "guild_id": guild_id,
            "urls": len(urls),
            "added": len(report.validated),
            "rejected": len(report.rejected),
            "unreadable": len(report.connectivity_failures),
            "duration_s": round(report.duration_s, 1),
            "lines": [result_line(r) for r in report.results][:5],
        }
    )
    return {
        "added": len(report.validated),
        "rejected": len(report.rejected),
        "unreadable": len(report.connectivity_failures),
        # Per-event detail the Discord bot needs to build cards + vote buttons.
        "events": [_event_summary(r, existing_ids, sink) for r in report.validated],
        "log": [result_line(r) for r in report.results],
        # Links whose failure a second try can fix — a page-load timeout or a
        # Chromium network error. The job queue retries only these (feature #26).
        "retryable_urls": retryable_urls(report.results),
        # Why each other link failed, as a class the bot words for people (#19).
        "failures": failures(report.results),
    }


@app.post("/api/ingest")
async def ingest(body: IngestBody, x_tenant_token: str | None = TenantToken):
    """Synchronous capture: returns when every URL is done (the bot's default path)."""
    _authorize_capture(body, x_tenant_token)
    urls = _ingest_urls(body)
    _check_capture_rate(body)
    _capture_limits.record(body.guild_id, body.user_id)
    return await _run_ingest(urls, body.guild_id)


# Async capture (ADR-0004): enqueue, then poll. Same validation, same result
# shape as /api/ingest — the bot opts in with INGEST_ASYNC=1. One worker unless
# INGEST_WORKERS says otherwise (Whisper is CPU-bound; two captures contend).
# CAPTURE_LOG=off turns the timing log off; otherwise finished jobs append one
# line each for scripts/summarize_captures.py (feature #23).
_capture_log = os.getenv("CAPTURE_LOG", "").strip() or "data/capture_jobs.jsonl"
# JOB_STORE=sqlite makes captures survive a restart (ADR-0005); the default
# stays in-memory (ADR-0004), so nothing changes for a self-host that hasn't
# asked for it. JOB_DB moves the file.
_job_store = (
    SqliteJobStore(os.getenv("JOB_DB", "").strip() or "data/jobs.db")
    if os.getenv("JOB_STORE", "").strip().lower() == "sqlite"
    else MemoryJobStore()
)
_jobs = JobQueue(
    _run_ingest,
    workers=int(os.getenv("INGEST_WORKERS", "").strip() or 1),
    log_path=None if _capture_log.lower() == "off" else Path(_capture_log),
    store=_job_store,
    # Retries for timed-out links and network errors only (feature #26);
    # INGEST_MAX_RETRIES=0 turns them off.
    max_retries=int(os.getenv("INGEST_MAX_RETRIES", "").strip() or 2),
    # Waiting jobs before a new one gets 429. Must stay under what the bot will
    # wait for (INGEST_WAIT_S ÷ capture time × workers) — see load_test_jobs.py.
    max_queued=int(os.getenv("INGEST_MAX_QUEUED", "").strip() or 50),
)

# Counts default to zero (disabled) until Nick approves the proposed values.
# Windows are configurable independently so changing one never changes another.
_capture_limits = CaptureRateLimiter(
    user_limit=int(os.getenv("CAPTURE_USER_LIMIT", "").strip() or 0),
    user_window_s=float(os.getenv("CAPTURE_USER_WINDOW_S", "").strip() or 600),
    server_limit=int(os.getenv("CAPTURE_SERVER_LIMIT", "").strip() or 0),
    server_window_s=float(os.getenv("CAPTURE_SERVER_WINDOW_S", "").strip() or 3600),
    daily_limit=int(os.getenv("CAPTURE_DAILY_LIMIT", "").strip() or 0),
    daily_window_s=float(os.getenv("CAPTURE_DAILY_WINDOW_S", "").strip() or 86400),
)


def _authorize_capture(body: IngestBody, token: str | None) -> None:
    authorize(body.guild_id, token, user_id=body.user_id)
    if body.guild_id and not body.user_id:
        raise HTTPException(400, "user_id is required for a server capture")


def _check_capture_rate(body: IngestBody) -> None:
    try:
        _capture_limits.check(body.guild_id, body.user_id)
    except CaptureRateLimitExceeded as exc:
        log.warning(
            "[capture_rate_limit] refused guild=%s user=%s limit=%s",
            body.guild_id,
            body.user_id,
            exc.limit_name,
        )
        raise HTTPException(
            429,
            f"capture rate limit exceeded ({exc.limit_name})",
            headers={"Retry-After": str(exc.retry_after_s)},
        ) from exc




@app.post("/api/jobs", status_code=202)
async def create_job(body: IngestBody, x_tenant_token: str | None = TenantToken):
    _authorize_capture(body, x_tenant_token)
    urls = _ingest_urls(body)
    # A second paste of the same reel, or the bot's Retry button, joins the
    # capture already in flight instead of starting a second one (feature #25).
    duplicate = _jobs.find_active(body.guild_id, urls) is not None
    if not duplicate:
        _check_capture_rate(body)
    try:
        job = _jobs.submit(urls, body.guild_id)
    except QueueFull as exc:
        raise HTTPException(429, f"capture queue is full — {exc}")
    if not duplicate:
        _capture_limits.record(body.guild_id, body.user_id)
    return {"job_id": job.id, "state": job.state, "duplicate": duplicate}


@app.get("/api/jobs")
def list_jobs(
    guild_id: str = "",
    state: str = "failed",
    limit: int = 20,
    x_tenant_token: str | None = TenantToken,
):
    """Failed captures for one server, newest first, with attempts and the last
    error — what an operator reads to see what isn't working (feature #26)."""
    authorize(guild_id, x_tenant_token, need=SCOPE_READ)
    if state != "failed":
        raise HTTPException(400, "only state=failed is supported")
    jobs = _jobs.failed(guild_id, limit=max(1, min(int(limit), 100)))
    # The links themselves are this tenant's own pastes, so they are safe to show
    # here — and without them nobody can act on a failure.
    return {"jobs": [{**j.to_dict(), "urls": j.urls} for j in jobs]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, x_tenant_token: str | None = TenantToken):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(
            404, "no such job — finished jobs expire after an hour, and a restart clears the queue"
        )
    # A finished job carries that guild's new spots — only its tenant may read it.
    authorize(job.guild_id, x_tenant_token, need=SCOPE_READ)
    return job.to_dict()


def _event_summary(result, existing_ids: set, sink: ChromaSink) -> dict:
    """Shape one validated record for the bot: id + display fields + live vote count.

    `new` distinguishes a first-time add from re-sharing an already-cataloged reel
    (the Chroma id is the content hash, so re-ingest upserts rather than duplicates).
    """
    cid = result.record.provenance.content_hash
    got = sink.collection.get(ids=[cid], include=["metadatas"])
    meta = (got["metadatas"][0] if got["ids"] else {}) or {}
    return {
        "id": cid,
        "venue": meta.get("venue_name") or result.record.venue_name,
        "category": meta.get("category") or result.record.category.value,
        "theme": meta.get("core_theme") or result.record.core_theme,
        "source_url": meta.get("source_url", ""),
        "image": meta.get("image_url", ""),
        "lat": meta.get("lat"),
        "lng": meta.get("lng"),
        "area": meta.get("raw_location_text", ""),   # for the card's map link (#36)
        "blurb": meta.get("summary", ""),          # video-based quick description ("" → "No info")
        "schedule": meta.get("schedule_status", "unscheduled"),
        "start_epoch": meta.get("start_epoch"),
        "end_epoch": meta.get("end_epoch"),
        "votes": int(meta.get("votes", 0)),
        "voters": sorted(_voters(meta).values()),
        "new": cid not in existing_ids,
    }


class ManualBody(BaseModel):
    venue: str
    theme: str
    source_url: str = ""
    guild_id: str = ""


def _manual_record(venue: str, theme: str, source_url: str = ""):
    """A validated EventInspiration from user-typed fields (the Discord modal).
    Pure — no sink, no network — so the mapping is testable offline."""
    from datetime import datetime, timezone

    from src.ingestion.pipeline.normalizer import _categorize
    from src.ingestion.schemas.inspiration import (
        EventInspiration, GeoContext, SourceProvenance, content_hash,
    )

    venue = venue.strip()[:110]
    theme = theme.strip()[:280]
    return EventInspiration(
        venue_name=venue,
        core_theme=theme,
        category=_categorize(f"{venue} {theme}".lower()),
        geo=GeoContext(),
        provenance=SourceProvenance(
            source_url=source_url.strip() or "manual://discord",
            platform="generic",
            fetched_at=datetime.now(timezone.utc),
            content_hash=content_hash(f"manual:{venue}:{theme}"),
            extractor="manual/1.0",
        ),
    )


@app.post("/api/manual")
def add_manual(body: ManualBody, x_tenant_token: str | None = TenantToken):
    """Manual catalog entry — the never-waste-a-paste fallback for failed captures."""
    authorize(body.guild_id, x_tenant_token)
    if not body.venue.strip() or len(body.theme.strip()) < 3:
        raise HTTPException(400, "venue and a short vibe description are required")
    try:
        record = _manual_record(body.venue, body.theme, body.source_url)
    except Exception as exc:
        raise HTTPException(400, f"couldn't build a valid spot from that: {exc}")
    sink = _sink_for(body.guild_id)
    event_id = sink.add(record)
    src = str(record.provenance.source_url)
    return {
        "id": event_id,
        "venue": record.venue_name,
        "category": record.category.value,
        "theme": record.core_theme,
        "source_url": "" if src.startswith("manual:") else src,
        "votes": 0,
        "new": True,
    }


class EditBody(BaseModel):
    venue: str
    theme: str
    guild_id: str = ""


@app.post("/api/events/{event_id}/edit")
def edit_event(event_id: str, body: EditBody, x_tenant_token: str | None = TenantToken):
    """User-facing edit (the card's ✏️ button): fix the venue name or vibe.

    Metadata updates always; the similarity embedding re-computes best-effort
    (Ollama down → the old vector stays until the next edit)."""
    authorize(body.guild_id, x_tenant_token)
    venue = body.venue.strip()[:110]
    theme = body.theme.strip()[:280]
    if not venue or len(theme) < 3:
        raise HTTPException(400, "venue and a short vibe description are required")
    sink = _sink_for(body.guild_id, create=False)
    res = sink.collection.get(ids=[event_id], include=["metadatas"])
    if not res["ids"]:
        raise HTTPException(404, "event not found")
    meta = res["metadatas"][0] or {}
    meta["venue_name"] = venue
    meta["core_theme"] = theme
    doc = (
        f"{theme}. Venue: {venue}. Category: {meta.get('category', 'other')}. "
        f"Vibe: {' '.join((meta.get('tags') or '').split(','))}".strip()
    )
    try:
        embedding = sink.embedder([doc])[0]
        sink.collection.update(
            ids=[event_id], metadatas=[meta], documents=[doc], embeddings=[embedding]
        )
    except Exception:   # embedder unreachable → metadata-only update
        sink.collection.update(ids=[event_id], metadatas=[meta])
    return {
        "id": event_id,
        "venue": venue,
        "theme": theme,
        "category": meta.get("category", "other"),
        "source_url": meta.get("source_url", ""),
        "image": meta.get("image_url", ""),
        "lat": meta.get("lat"),
        "lng": meta.get("lng"),
        "blurb": meta.get("summary", ""),
        "start_epoch": meta.get("start_epoch"),
        "end_epoch": meta.get("end_epoch"),
        "votes": int(meta.get("votes", 0)),
        "voters": sorted(_voters(meta).values()),
    }


# ── The went-there loop (the "100 Nights Out" counter) ──────────────────────
# lock → (time passes) → followup prompt → two "we went" confirmations →
# an attended night. Everything lives in the event's metadata, so the counter
# is exactly as multi-tenant as the catalog.

_FOLLOWUP_DELAY_S = 8 * 3600   # ask the morning after, not the second it ends


class LockBody(BaseModel):
    guild_id: str = ""
    channel_id: str = ""
    end_epoch: int
    discord_event_id: str = ""


@app.post("/api/events/{event_id}/lock")
def lock_event(event_id: str, body: LockBody, x_tenant_token: str | None = TenantToken):
    """Record that this spot became a real scheduled event (LockInButton)."""
    authorize(body.guild_id, x_tenant_token)
    sink = _sink_for(body.guild_id, create=False)
    res = sink.collection.get(ids=[event_id], include=["metadatas"])
    if not res["ids"]:
        raise HTTPException(404, "event not found")
    meta = res["metadatas"][0] or {}
    meta["locked_end_epoch"] = int(body.end_epoch)
    meta["locked_channel_id"] = body.channel_id
    if body.discord_event_id:
        meta["discord_event_id"] = body.discord_event_id
    meta["went_prompted"] = False
    sink.collection.update(ids=[event_id], metadatas=[meta])
    return {"id": event_id, "locked": True}


@app.get("/api/followups")
def followups(guild_id: str = "", now: int = 0, x_tenant_token: str | None = TenantToken):
    """Locked events whose night has passed and haven't been asked about yet.
    Each is returned exactly once (marked prompted here)."""
    # A write scope, not read: this call marks events as prompted, so a stranger
    # calling it would silently swallow a server's went-there prompts.
    authorize(guild_id, x_tenant_token)
    now = now or int(time.time())
    sink = _sink_for(guild_id, create=False)
    res = sink.collection.get(include=["metadatas"])
    due = []
    for event_id, m in zip(res["ids"], res["metadatas"]):
        m = m or {}
        end = m.get("locked_end_epoch")
        if not end or m.get("went_prompted") or now < int(end) + _FOLLOWUP_DELAY_S:
            continue
        m["went_prompted"] = True
        sink.collection.update(ids=[event_id], metadatas=[m])
        due.append(
            {
                "id": event_id,
                "venue": m.get("venue_name", "that spot"),
                "channel_id": m.get("locked_channel_id", ""),
            }
        )
    return {"due": due}


class WentBody(BaseModel):
    user_id: str
    user_name: str = ""
    happened: bool = True


@app.post("/api/events/{event_id}/went")
def went(
    event_id: str,
    body: WentBody,
    guild_id: str = "",
    x_tenant_token: str | None = TenantToken,
):
    """A 'we went' confirmation. Two distinct users → an official attended night."""
    # Signed over user_id: two confirmations from *different* people is the whole
    # counter, so a caller must not be able to confirm as someone else.
    authorize(guild_id, x_tenant_token, user_id=body.user_id)
    sink = _sink_for(guild_id, create=False)
    res = sink.collection.get(ids=[event_id], include=["metadatas"])
    if not res["ids"]:
        raise HTTPException(404, "event not found")
    meta = res["metadatas"][0] or {}
    if body.happened:
        went_map = _voters({"voters": meta.get("went")})   # same JSON-map shape as votes
        went_map[body.user_id] = body.user_name or body.user_id
        meta["went"] = json.dumps(went_map)
        meta["attended"] = len(went_map) >= 2
    else:
        meta["went_dismissed"] = True
    sink.collection.update(ids=[event_id], metadatas=[meta])
    confirmations = len(_voters({"voters": meta.get("went")}))
    return {
        "id": event_id,
        "venue": meta.get("venue_name", ""),
        "confirmations": confirmations,
        "attended": bool(meta.get("attended")),
        "nights": _count_nights(sink),
    }


def _count_nights(sink: ChromaSink) -> int:
    res = sink.collection.get(include=["metadatas"])
    return sum(1 for m in res["metadatas"] if (m or {}).get("attended"))


@app.get("/api/nights")
def nights(guild_id: str = "", x_tenant_token: str | None = TenantToken):
    """The counter that matters: confirmed real-world nights out."""
    authorize(guild_id, x_tenant_token, need=SCOPE_READ)
    return {"nights": _count_nights(_sink_for(guild_id, create=False))}


# ── Per-server settings (the /setup wizard, Track C #8) ─────────────────────
# One file per server (serving/guild_settings.py), apart from the catalog.

_guild_settings = GuildSettingsStore(Path("data/guild_settings"))


def _geocode_city(city: str):
    """(lat, lng) for a home city, or None. One OpenStreetMap lookup per /setup
    save (#36) — the only geocoding the admin app does."""
    from src.services.location_service import address_to_coords

    lat, lng = address_to_coords(city)
    return (lat, lng) if lat is not None and lng is not None else None


class SettingsBody(BaseModel):
    guild_id: str
    drop_channel_id: str | None = None
    home_city: str | None = None


@app.get("/api/settings")
def get_settings(guild_id: str = "", x_tenant_token: str | None = TenantToken):
    """A server's /setup settings; the defaults if it never ran /setup."""
    # The full token, not a share token: settings are for the bot, and share
    # links go to people outside the server.
    authorize(guild_id, x_tenant_token)
    try:
        return {"settings": _guild_settings.get(guild_id)}
    except SettingsError as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/settings")
def put_settings(body: SettingsBody, x_tenant_token: str | None = TenantToken):
    """Save what the wizard changed. Only the fields sent are touched, and the
    catalog never is, so re-running /setup can't wipe a server's spots."""
    authorize(body.guild_id, x_tenant_token)
    changes = {k: getattr(body, k) for k in body.model_fields_set if k != "guild_id"}
    try:
        _guild_settings.path_for(body.guild_id)          # a bad server id: 400 before any lookup
        if "home_city" in changes:
            # Where the city is, for distance on cards (#36) — checked first, so a
            # rejected value is never sent out. A failed lookup saves no
            # coordinates; cards then just leave the distance out.
            city = clean_city(changes["home_city"] or "")
            spot = _geocode_city(city) if city else None
            changes["home_lat"], changes["home_lng"] = spot if spot else (None, None)
        return {"settings": _guild_settings.update(body.guild_id, changes)}
    except SettingsError as exc:
        raise HTTPException(400, str(exc))


# ── Feedback and the SUS survey (Track C #20) ───────────────────────────────
# Typed by the person on purpose, stored without a user id (serving/feedback.py).

_feedback = FeedbackStore(Path("data/feedback"))


class FeedbackBody(BaseModel):
    guild_id: str
    kind: str
    text: str


class SurveyBody(BaseModel):
    guild_id: str
    answers: list[int]
    participant: str = ""


@app.post("/api/feedback")
def post_feedback(body: FeedbackBody, x_tenant_token: str | None = TenantToken):
    authorize(body.guild_id, x_tenant_token)
    try:
        _feedback.add_feedback(body.guild_id, body.kind, body.text)
    except FeedbackError as exc:
        raise HTTPException(400, str(exc))
    return {"saved": True}


@app.post("/api/survey")
def post_survey(body: SurveyBody, x_tenant_token: str | None = TenantToken):
    authorize(body.guild_id, x_tenant_token)
    try:
        row = _feedback.add_sus(body.guild_id, body.answers, body.participant)
    except FeedbackError as exc:
        raise HTTPException(400, str(exc))
    return {"score": row["score"]}


@app.get("/api/survey")
def survey_summary(guild_id: str = "", x_tenant_token: str | None = TenantToken):
    """SUS responses for one server: count, mean, SD and each score — no answers
    by name, because none are stored by name."""
    authorize(guild_id, x_tenant_token)
    try:
        return _feedback.sus_summary(guild_id)
    except FeedbackError as exc:
        raise HTTPException(400, str(exc))


# ── Time-to-card (Track C #18) ──────────────────────────────────────────────
# The bot reports how long each paste took to become a card. Stored without a
# server id (serving/time_to_card.py); TIME_TO_CARD_LOG=off turns it off.

_ttc_log = os.getenv("TIME_TO_CARD_LOG", "").strip() or "data/time_to_card.jsonl"
_time_to_card = TimeToCardLog(None if _ttc_log.lower() == "off" else Path(_ttc_log))


class TimeToCardBody(BaseModel):
    guild_id: str
    seconds: float
    outcome: str
    links: int = 1


@app.post("/api/time-to-card")
def post_time_to_card(body: TimeToCardBody, x_tenant_token: str | None = TenantToken):
    # A real server's token is required so nobody can pad the numbers, even
    # though the server id itself isn't stored.
    authorize(body.guild_id, x_tenant_token)
    if not body.guild_id:
        raise HTTPException(400, "time-to-card is reported for a server")
    try:
        _time_to_card.record(body.seconds, body.outcome, body.links)
    except TimingError as exc:
        raise HTTPException(400, str(exc))
    return {"saved": _time_to_card.path is not None}


# ── Delete a server's data (Track C #21, threat model T6) ───────────────────


class ForgetBody(BaseModel):
    guild_id: str
    confirm: str = ""     # must repeat guild_id, so a typo or a stray call deletes nothing


def _purge_catalog(guild_id: str) -> dict:
    """The catalog and the server's own files (serving/forget.py). Split out so
    the authorization tests and the bench can stub it."""
    catalog = data_purge.purge_catalog(
        Path(_settings.chroma_path), collection_for_guild(_settings, guild_id)
    )
    _sinks.pop(str(guild_id), None)            # its collection handle is dead now
    return {
        "spots": catalog["spots"],
        "vacuumed": catalog["vacuumed"],
        "index_dirs_left": catalog["index_dirs_left"],
        "shared_file_rows": data_purge.scrub_shared_jsonl(
            _LEGACY_JSONL, catalog["ids"] - catalog["others"]
        ),
        "shared_posts_kept": len(catalog["ids"] & catalog["others"]),
        **data_purge.purge_files(guild_id, jsonl_dir=_JSONL_DIR, raw_root=_RAW_ROOT),
    }


@app.post("/api/forget")
async def forget_server(body: ForgetBody, x_tenant_token: str | None = TenantToken):
    """Delete everything kept for one server: spots, votes, settings, capture
    history. Irreversible.

    `async` on purpose: it runs on the event loop and never awaits, so no
    capture worker can start a job or append a log line halfway through.
    """
    authorize(body.guild_id, x_tenant_token)
    gid = body.guild_id
    if not gid:
        raise HTTPException(400, "the shared catalog can't be deleted this way")
    if body.confirm != gid:
        raise HTTPException(400, "confirm must repeat the server id")
    if _capturing.get(gid) or _jobs.active_for(gid):
        raise HTTPException(409, "a capture for this server is still running — try again once it finishes")
    report = {"catalog": _purge_catalog(gid)}
    report["settings"] = _guild_settings.delete(gid)
    report["feedback_rows"] = _feedback.delete(gid)
    report["jobs"] = _jobs.forget_guild(gid)
    kept = [entry for entry in _CAPTURE_LOG if entry.get("guild_id") != gid]
    report["activity_entries"] = len(_CAPTURE_LOG) - len(kept)
    _CAPTURE_LOG.clear()
    _CAPTURE_LOG.extend(kept)
    _capture_limits.forget_guild(gid)
    log.info("[forget] deleted server %s: %d spot(s)", gid, report["catalog"]["spots"])
    return report


@app.delete("/api/events/{event_id}")
def delete_event(event_id: str, guild_id: str = "", x_tenant_token: str | None = TenantToken):
    authorize(guild_id, x_tenant_token)
    _sink_for(guild_id, create=False).collection.delete(ids=[event_id])
    return {"deleted": event_id}


@app.post("/api/events/{event_id}/vote")
def vote(
    event_id: str,
    body: VoteBody,
    guild_id: str = "",
    x_tenant_token: str | None = TenantToken,
):
    # user_id is part of the signed payload, so the token proves the caller may
    # vote as *this* user — not merely that they may touch this tenant.
    authorize(guild_id, x_tenant_token, user_id=body.user_id)
    sink = _sink_for(guild_id, create=False)
    res = sink.collection.get(ids=[event_id], include=["metadatas"])
    if not res["ids"]:
        raise HTTPException(404, "event not found")
    meta = _apply_vote(res["metadatas"][0] or {}, body)
    sink.collection.update(ids=[event_id], metadatas=[meta])
    return {
        "id": event_id,
        "votes": meta["votes"],
        "voters": sorted(_voters(meta).values()),
    }


@app.get("/health")
def health():
    """Liveness for the staging healthcheck (#11): answers without touching Chroma,
    Ollama or the network, so it's cheap enough to poll every 30 s."""
    return {"ok": True, "queued_or_running": _jobs.pending}


@app.get("/api/stats")
def stats():
    """Behind-the-scenes: services, per-server catalogs, and recent captures."""
    # Services (2s probes — the dash must never hang)
    def _probe(url: str) -> bool:
        try:
            import httpx

            return httpx.get(url, timeout=2.0).status_code < 500
        except Exception:
            return False

    services = {
        "admin": True,   # we answered this request
        "ollama": _probe(f"{_settings.ollama_url}/api/tags"),
        "recommend": _probe(
            os.getenv("RECOMMEND_URL", "http://localhost:8003").rstrip("/") + "/health"
        ),
        "transcriber": _transcriber is not None,
        "ocr": _ocr_reader is not None,
        "authed_ig": _ig_source is not None,
    }

    # Tenants: every catalog collection in the store
    client = _sink_for("").client
    prefix = _settings.chroma_collection
    tenants = []
    totals = {"spots": 0, "votes": 0, "nights": 0}
    for col in client.list_collections():
        name = getattr(col, "name", str(col))
        if not name.startswith(prefix):
            continue
        collection = client.get_collection(name)
        metas = collection.get(include=["metadatas"])["metadatas"] or []
        spots = len(metas)
        votes = sum(int((m or {}).get("votes", 0)) for m in metas)
        nights_count = sum(1 for m in metas if (m or {}).get("attended"))
        last = max((str((m or {}).get("fetched_at", "")) for m in metas), default="")
        gid = name[len(prefix):].lstrip("_").lstrip("g") or "default"
        tenants.append(
            {"guild": gid, "spots": spots, "votes": votes, "nights": nights_count, "last": last[:16]}
        )
        totals["spots"] += spots
        totals["votes"] += votes
        totals["nights"] += nights_count
    tenants.sort(key=lambda t: -t["spots"])

    return {
        "services": services,
        "totals": {**totals, "servers": len(tenants)},
        "tenants": tenants,
        # Counts only. _CAPTURE_LOG entries also carry `lines` — each capture's URL,
        # venue and coordinates — which this unauthenticated, cross-server view must
        # not return (the page never used them). Found building #29's panels.
        "captures": [
            {k: v for k, v in entry.items() if k != "lines"} for entry in _CAPTURE_LOG
        ],
        # #29: capture health from the #23 timing log, and the queue and limits as
        # they stand now. Counts and seconds only — no links, ids or error text.
        "capture_health": capture_stats.summarize(
            capture_stats.read_rows(_jobs.log_path, max_bytes=2_000_000)
        ) if _jobs.log_path is not None else None,
        "queue": _jobs.snapshot(),
        "limits": _capture_limits.settings(),
        # #18: paste → card as users see it (median / p95), None when not logged.
        "time_to_card": _time_to_card.summary(),
    }


@app.get("/dash", response_class=HTMLResponse)
def dash():
    """Operator dashboard — the behind-the-scenes view of the whole service."""
    return _DASH_PAGE


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


@app.get("/share", response_class=HTMLResponse)
def share(guild_id: str = "", t: str | None = None):
    """Read-only catalog page — safe to send to people outside the server.

    The growth surface (docs/PRODUCT_ROADMAP.md 2.3): viewable without the bot
    installed, no add/vote/remove controls, with an install call-to-action.

    The link IS the capability: `?guild_id=…&t=…` carries a read-scoped tenant
    token, so a recipient sees exactly one catalog and cannot swap the guild_id
    to enumerate others. The bot's /share command mints it; so does
    scripts/make_share_link.py. Rotating SPOTBOT_SIGNING_KEY revokes every link.
    """
    authorize(guild_id, t, need=SCOPE_READ)
    # Tokens are hex; anything else is dropped rather than injected into the page.
    token = t if (t and all(c in "0123456789abcdef" for c in t)) else ""
    return _SHARE_PAGE.replace("__TENANT_TOKEN__", token)


# ── UI ───────────────────────────────────────────────────────────────────────

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SocialAgent — Event Catalog</title>
<style>
  :root{--bg:#111318;--card:#1A1D24;--line:#272B34;--txt:#E7E9EE;--mut:#8B92A0;--acc:#6EA8FE;--ok:#3FB950;--bad:#F85149}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,Segoe UI,sans-serif}
  header{padding:20px 24px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:19px;font-weight:700;letter-spacing:-.01em} .sub{color:var(--mut);font-size:12.5px;margin-top:4px}
  main{max-width:860px;margin:0 auto;padding:24px}
  .count,.votes b{font-variant-numeric:tabular-nums}
  .add{display:flex;gap:8px;margin-bottom:8px}
  textarea{flex:1;background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:10px;font:inherit;min-height:44px;resize:vertical}
  button{background:var(--acc);color:#06121f;border:0;border-radius:8px;padding:0 16px;font-weight:600;cursor:pointer}
  button:disabled{opacity:.5;cursor:wait}
  .hint{color:var(--mut);font-size:12px;margin-bottom:18px}
  #status{min-height:20px;font-size:13px;margin:8px 0;white-space:pre-wrap}
  .count{color:var(--mut);font-size:13px;margin:16px 0 8px}
  .ev{display:flex;gap:14px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:10px}
  .votes{display:flex;flex-direction:column;align-items:center;min-width:46px}
  .votes b{font-size:18px} .vb{background:none;border:1px solid var(--line);color:var(--mut);border-radius:6px;width:34px;padding:2px 0;font-size:14px;margin:1px 0}
  .vb:hover{color:var(--txt);border-color:var(--acc)}
  .meta{flex:1;min-width:0}
  .venue{font-weight:700;font-size:16px}
  .badge{display:inline-block;background:#26304a;color:var(--acc);border-radius:999px;padding:1px 9px;font-size:11px;margin-left:8px;vertical-align:middle}
  .theme{color:var(--mut);font-size:13px;margin:4px 0;overflow:hidden;text-overflow:ellipsis}
  .row{font-size:12px;color:var(--mut)} .row a{color:var(--acc);text-decoration:none}
  .del{background:none;border:1px solid var(--line);color:var(--bad);border-radius:6px;padding:6px 10px;cursor:pointer;align-self:center}
  .del:hover{border-color:var(--bad)}
  .empty{color:var(--mut);text-align:center;padding:40px}
  .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
</style></head><body>
<header><h1><span aria-hidden="true">🎟️</span> SocialAgent — Event Catalog</h1>
<div class="sub">Add public post URLs, curate the list, and vote. Changes are live to the Discord bot.</div></header>
<main>
  <div class="add">
    <textarea id="urls" aria-label="Post links to add, one per line" placeholder="Paste one or more public post URLs (one per line)…"></textarea>
    <button id="addBtn" onclick="addUrls()">Add</button>
  </div>
  <div class="hint">Ingesting runs the full pipeline (fetch → LLM → schedule) — ~20–30s per URL. Login-walled or expired posts are skipped.</div>
  <div id="status" role="status" aria-live="polite"></div>
  <details style="margin-bottom:16px">
    <summary style="cursor:pointer;color:var(--acc);font-size:13.5px;user-select:none">✏️ Add a spot manually (no link needed)</summary>
    <div style="margin-top:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;display:flex;flex-direction:column;gap:8px">
      <input id="mVenue" aria-label="Venue name" placeholder="Venue name (e.g. Wasteland, Casa Loma, The Pike)" style="background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:8px 10px;font:inherit"/>
      <textarea id="mTheme" aria-label="What the place is like" placeholder="Describe the vibe / what happens here…" style="background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:8px 10px;font:inherit;min-height:60px;resize:vertical"></textarea>
      <input id="mUrl" aria-label="Source link (optional)" placeholder="Source URL (optional)" style="background:var(--bg);border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:8px 10px;font:inherit"/>
      <div style="display:flex;gap:8px;align-items:center">
        <button id="mBtn" onclick="addManual()" style="background:var(--acc);color:#0D1117;border:none;border-radius:6px;padding:8px 16px;cursor:pointer;font-weight:600">Add spot</button>
        <span id="mStatus" role="status" aria-live="polite" style="font-size:13px;color:var(--mut)"></span>
      </div>
    </div>
  </details>
  <div class="count" id="count"></div>
  <div id="list"></div>
</main>
<script>
const EMOJI={food_drink:"🍽️",cafe_dessert:"🍰",nightlife:"🍸",live_music:"🎶",market_popup:"🛍️",outdoors:"🏞️",community:"🤝",other:"📍"};
async function addManual(){
  const venue=document.getElementById('mVenue').value.trim();
  const theme=document.getElementById('mTheme').value.trim();
  const url=document.getElementById('mUrl').value.trim();
  const btn=document.getElementById('mBtn'); const st=document.getElementById('mStatus');
  if(!venue||theme.length<3){st.style.color='var(--bad)';st.textContent='Venue name and a short vibe description are required.';return;}
  btn.disabled=true; st.style.color='var(--mut)'; st.textContent='Adding…';
  try{
    const r=await fetch('/api/manual',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({venue,theme,source_url:url,guild_id:''})});
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||'Error');
    st.style.color='var(--ok)'; st.textContent=`✅ Added "${d.venue}" (${d.category.replace(/_/g,' ')})`;
    document.getElementById('mVenue').value='';
    document.getElementById('mTheme').value='';
    document.getElementById('mUrl').value='';
    load();
  }catch(e){st.style.color='var(--bad)';st.textContent='❌ '+e;}
  btn.disabled=false;
}
async function load(){
  const r=await fetch('/api/events'); const d=await r.json();
  document.getElementById('count').textContent=d.count+' event'+(d.count===1?'':'s')+' in the catalog';
  const list=document.getElementById('list');
  if(!d.events.length){list.innerHTML='<div class="empty">No events yet — add a URL above.</div>';return;}
  list.innerHTML=d.events.map(e=>`
    <div class="ev">
      <div class="votes">
        <button class="vb" onclick="vote('${e.id}',1)" aria-label="Vote up ${esc(e.venue)}">▲</button>
        <b>${e.votes}<span class="sr"> votes</span></b>
        <button class="vb" onclick="vote('${e.id}',-1)" aria-label="Vote down ${esc(e.venue)}">▼</button>
      </div>
      <div class="meta">
        <div><span class="venue">${esc(e.venue)}</span><span class="badge"><span aria-hidden="true">${EMOJI[e.category]||'📍'}</span> ${esc(e.category.replace(/_/g,' '))}</span></div>
        <div class="theme">${esc(e.theme||'')}</div>
        <div class="row">${e.schedule==='scheduled'&&e.start_utc?('<span aria-hidden="true">🗓️</span> '+new Date(e.start_utc).toLocaleString()+' · '):''}${e.source_url?`<a href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">source ↗<span class="sr"> for ${esc(e.venue)} (opens in a new tab)</span></a>`:''}</div>
      </div>
      <button class="del" onclick="del('${e.id}')" aria-label="Remove ${esc(e.venue)}"><span aria-hidden="true">🗑</span> Remove</button>
    </div>`).join('');
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function addUrls(){
  const ta=document.getElementById('urls'); const btn=document.getElementById('addBtn'); const st=document.getElementById('status');
  const urls=ta.value.split('\\n').map(s=>s.trim()).filter(Boolean);
  if(!urls.length){st.textContent='Paste at least one URL.';return;}
  btn.disabled=true; st.textContent='⏳ Ingesting '+urls.length+' URL(s)… (~20–30s each)';
  try{
    const r=await fetch('/api/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls})});
    const d=await r.json();
    st.textContent=`✅ added ${d.added} · ⚠️ rejected ${d.rejected} · 🚫 unreadable ${d.unreadable}\\n`+(d.log||[]).join('\\n');
    ta.value='';
  }catch(e){st.textContent='❌ '+e;}
  btn.disabled=false; load();
}
async function vote(id,delta){await fetch('/api/events/'+id+'/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delta})});load();}
async function del(id){if(!confirm('Remove this event?'))return;await fetch('/api/events/'+id,{method:'DELETE'});load();}
load();
</script>
</body></html>"""


_SHARE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="referrer" content="no-referrer"/>
<title>SpotBot — our spots</title>
<style>
  :root{--bg:#111318;--card:#1A1D24;--line:#272B34;--txt:#E7E9EE;--mut:#8B92A0;--acc:#6EA8FE;--ok:#3FB950}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,Segoe UI,sans-serif}
  header{padding:20px 24px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:19px;font-weight:700;letter-spacing:-.01em} .sub{color:var(--mut);font-size:12.5px;margin-top:4px}
  main{max-width:860px;margin:0 auto;padding:24px}
  .count{color:var(--mut);font-size:13px;margin:0 0 12px}
  .ev{display:flex;gap:14px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:10px}
  .thumb{width:72px;height:72px;border-radius:8px;object-fit:cover;background:#26304a;flex:none}
  .votes{min-width:56px;text-align:center;color:var(--ok);font-weight:700;font-size:16px;align-self:center}
  .votes .who{display:block;color:var(--mut);font-weight:400;font-size:11px}
  .meta{flex:1;min-width:0}
  .venue{font-weight:700;font-size:16px}
  .badge{display:inline-block;background:#26304a;color:var(--acc);border-radius:999px;padding:1px 9px;font-size:11px;margin-left:8px;vertical-align:middle}
  .theme{color:var(--mut);font-size:13px;margin:4px 0;overflow:hidden;text-overflow:ellipsis}
  .row{font-size:12px;color:var(--mut)} .row a{color:var(--acc);text-decoration:none}
  .empty{color:var(--mut);text-align:center;padding:40px}
  footer{max-width:860px;margin:0 auto;padding:8px 24px 32px;color:var(--mut);font-size:13px}
  footer a{color:var(--acc);text-decoration:none;font-weight:600}
  .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
</style></head><body>
<header><h1><span aria-hidden="true">📍</span> Our spots</h1>
<div class="sub">A group catalog of places we want to go — captured from shared reels.</div></header>
<main>
  <div class="count" id="count"></div>
  <div id="list"></div>
</main>
<footer>Powered by <a href="https://github.com/NickNojiri/SocialAgent-Team10" target="_blank" rel="noopener noreferrer">SpotBot<span class="sr"> (opens in a new tab)</span></a>
— paste a reel in Discord, get a votable spot. Add it to your server.</footer>
<script>
const EMOJI={food_drink:"🍽️",cafe_dessert:"🍰",nightlife:"🍸",live_music:"🎶",market_popup:"🛍️",outdoors:"🏞️",community:"🤝",other:"📍"};
const GUILD=new URLSearchParams(location.search).get('guild_id')||'';
const TOKEN="__TENANT_TOKEN__";   // read-scoped, injected by GET /share
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function load(){
  const r=await fetch('/api/events?guild_id='+encodeURIComponent(GUILD),{headers:TOKEN?{'X-Tenant-Token':TOKEN}:{}});
  const list=document.getElementById('list');
  if(!r.ok){list.innerHTML='<div class="empty">This link is no longer valid — ask for a fresh one with /share.</div>';return;}
  const d=await r.json();
  document.getElementById('count').textContent=d.count+' spot'+(d.count===1?'':'s');
  if(!d.events.length){list.innerHTML='<div class="empty">Nothing here yet.</div>';return;}
  list.innerHTML=d.events.map(e=>`
    <div class="ev">
      ${e.image?`<img class="thumb" src="${esc(e.image)}" alt="Photo from the post about ${esc(e.venue)}"/>`:''}
      <div class="meta">
        <div><span class="venue">${esc(e.venue)}</span><span class="badge"><span aria-hidden="true">${EMOJI[e.category]||'📍'}</span> ${esc((e.category||'other').replace(/_/g,' '))}</span></div>
        <div class="theme">${esc(e.theme||'')}</div>
        <div class="row">
          ${e.schedule==='scheduled'&&e.start_utc?('<span aria-hidden="true">🗓️</span> '+new Date(e.start_utc).toLocaleString()+' · '):''}
          ${(e.lat!=null&&e.lng!=null)?`<a href="https://www.openstreetmap.org/?mlat=${e.lat}&mlon=${e.lng}#map=17/${e.lat}/${e.lng}" target="_blank" rel="noopener noreferrer">map ↗<span class="sr"> of ${esc(e.venue)} (opens in a new tab)</span></a> · `:''}
          ${e.source_url?`<a href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">source ↗<span class="sr"> for ${esc(e.venue)} (opens in a new tab)</span></a>`:''}
        </div>
      </div>
      <div class="votes"><span aria-hidden="true">👍</span> ${e.votes}<span class="sr"> vote${e.votes===1?'':'s'}</span><span class="who">${esc((e.voters||[]).slice(0,4).join(', '))}</span></div>
    </div>`).join('');
}
load();
</script>
</body></html>"""


_DASH_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SpotBot — Operations</title>
<style>
  :root{--bg:#111318;--panel:#1A1D24;--line:#272B34;--txt:#E7E9EE;--mut:#8B92A0;
        --acc:#6EA8FE;--ok:#3FB950;--bad:#F85149;--warn:#E3B341;--num:#F0F2F7}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.55 system-ui,"Segoe UI",sans-serif}
  main{max-width:1060px;margin:0 auto;padding:28px 24px 64px}
  h1{font-size:19px;font-weight:700;letter-spacing:-.01em;margin:0;display:inline}
  .sub{color:var(--mut);font-size:12px;margin-top:3px}
  h2{font-size:10.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--mut);margin:28px 0 9px}
  /* pulse ring on h1 when refreshing */
  #hdr{display:flex;align-items:center;gap:10px}
  #pulse{width:8px;height:8px;border-radius:50%;background:var(--ok);flex-shrink:0;transition:opacity .15s}
  #pulse.spin{opacity:.3}
  #next{font-size:11px;color:var(--mut);margin-left:auto;font-variant-numeric:tabular-nums}
  /* metric tiles */
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-top:18px}
  .tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px;position:relative}
  .tile b{display:block;font-size:28px;font-weight:700;color:var(--num);
          font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .tile span{font-size:11px;color:var(--mut);letter-spacing:.04em;text-transform:uppercase}
  .tile .delta{position:absolute;top:12px;right:12px;font-size:11px;font-weight:600}
  .up{color:var(--ok)} .dn{color:var(--bad)}
  /* service chips */
  .chips{display:flex;flex-wrap:wrap;gap:8px}
  .chip{display:inline-flex;align-items:center;gap:7px;background:var(--panel);border:1px solid var(--line);
        border-radius:999px;padding:5px 13px;font-size:12.5px;transition:border-color .3s}
  .dot{width:7px;height:7px;border-radius:50%;transition:background .3s}
  .on .dot{background:var(--ok)} .off .dot{background:var(--bad)}
  .on{border-color:var(--ok)22} .off{color:var(--mut);border-color:var(--bad)22}
  /* ML layer badges */
  .ml-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:4px}
  .ml{display:inline-flex;align-items:center;gap:6px;background:var(--panel);border:1px solid var(--acc)33;
      border-radius:8px;padding:6px 12px;font-size:12.5px;color:var(--acc)}
  .ml .icon{font-size:15px}
  /* tenant table */
  .wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
  table{border-collapse:collapse;width:100%;font-size:13px}
  th{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);text-align:left;
     padding:9px 14px 6px;border-bottom:1px solid var(--line);font-weight:600}
  td{padding:8px 14px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
  tr:last-child td{border-bottom:0}
  td.num{text-align:right} th.num{text-align:right}
  .bar-cell{width:90px}
  .bar-bg{height:5px;border-radius:3px;background:var(--line);overflow:hidden}
  .bar-fill{height:100%;border-radius:3px;background:var(--acc);transition:width .4s}
  /* capture feed */
  .feed{display:flex;flex-direction:column;gap:7px}
  .cap{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;
       display:flex;gap:12px;align-items:baseline;font-size:13px;animation:fadeIn .3s}
  @keyframes fadeIn{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
  .cap time{color:var(--mut);font-size:12px;min-width:56px;font-variant-numeric:tabular-nums}
  .cap .g{color:var(--acc);min-width:80px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .cap .r{color:var(--mut);margin-left:auto;white-space:nowrap}
  .ok-n{color:var(--ok);font-weight:600} .bad-n{color:var(--bad);font-weight:600}
  .empty{color:var(--mut);padding:20px;text-align:center}
  footer{color:var(--mut);font-size:11.5px;margin-top:24px}
  /* progress bar animates down to 0 between refreshes */
  #pgbar{height:2px;background:var(--acc);position:fixed;top:0;left:0;transition:width linear 5s;z-index:99}
  .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
</style></head><body>
<div id="pgbar" style="width:100%" aria-hidden="true"></div>
<main>
  <div id="hdr">
    <div id="pulse" aria-hidden="true"></div>
    <h1>SpotBot — live ops</h1>
    <span id="next">next refresh in 5s</span>
  </div>
  <div class="sub">Auto-refreshes every 5 seconds · <a style="color:var(--acc)" href="/">catalog admin</a> · <a style="color:var(--acc)" href="/share">share page</a></div>

  <div class="tiles" id="tiles"></div>

  <h2>Services</h2>
  <div class="chips" id="chips"></div>

  <h2>ML Layers active</h2>
  <div class="ml-row" id="ml"></div>

  <h2>Servers</h2>
  <div class="wrap"><table id="tenants"></table></div>

  <h2>Time to card</h2>
  <div class="tiles" id="ttc"></div>

  <h2>Capture health</h2>
  <div class="tiles" id="health"></div>
  <div class="wrap"><table id="stages"></table></div>

  <h2>Queue &amp; limits</h2>
  <div class="chips" id="queue"></div>

  <h2>Security</h2>
  <div class="wrap"><div class="empty" id="security">Reserved for Track D's abuse &amp;
    intrusion monitoring (#32): refused authorizations, rate-limit hits, blocked hosts.
    Counts only, like everything on this page.</div></div>

  <h2>Recent captures</h2>
  <div class="feed" id="feed"></div>

  <footer>in-memory activity log · last 50 runs · resets with admin app restart</footer>
</main>
<script>
const SVC_LABELS={admin:"Admin API",ollama:"Ollama LLM",recommend:"Recommend svc",transcriber:"Whisper audio",ocr:"Cover OCR",authed_ig:"Authed IG"};
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

let prev={};
let countdown=5;
let countEl,pgEl,pulseEl;

function startBar(){
  pgEl.style.transition='none'; pgEl.style.width='100%';
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    pgEl.style.transition='width linear 5s'; pgEl.style.width='0%';
  }));
}

function delta(key,cur){
  const p=prev[key]; prev[key]=cur;
  if(p==null||p===cur) return '';
  return cur>p?`<span class="delta up">+${cur-p}</span>`:`<span class="delta dn">${cur-p}</span>`;
}

async function load(){
  pulseEl.classList.add('spin');
  let d; try{ d=await (await fetch('/api/stats')).json(); }catch(e){ pulseEl.classList.remove('spin'); return; }
  pulseEl.classList.remove('spin');

  const t=d.totals||{};
  document.getElementById('tiles').innerHTML=`
    <div class="tile"><b>${t.servers??0}</b>${delta('servers',t.servers??0)}<span>Discord servers</span></div>
    <div class="tile"><b>${t.spots??0}</b>${delta('spots',t.spots??0)}<span>spots cataloged</span></div>
    <div class="tile"><b>${t.votes??0}</b>${delta('votes',t.votes??0)}<span>votes cast</span></div>
    <div class="tile"><b>${t.nights??0}</b>${delta('nights',t.nights??0)}<span>nights out</span></div>
    <div class="tile"><b>${(d.captures||[]).length}</b><span>captures (session)</span></div>`;

  // The dot's color is repeated in words for screen readers (WCAG 1.4.1).
  document.getElementById('chips').innerHTML=Object.entries(d.services||{}).map(([k,up])=>
    `<span class="chip ${up?'on':'off'}"><span class="dot" aria-hidden="true"></span>${SVC_LABELS[k]||k}<span class="sr">${up?' — up':' — down'}</span></span>`).join('');

  document.getElementById('ml').innerHTML=`
    <span class="ml"><span class="icon" aria-hidden="true">🔢</span>mxbai-embed-large · 1024-dim</span>
    <span class="ml"><span class="icon" aria-hidden="true">🔁</span>LLM cross-encoder re-ranking</span>
    <span class="ml"><span class="icon" aria-hidden="true">👤</span>User taste profile (α=0.25)</span>`;

  const maxSpots=Math.max(1,...(d.tenants||[]).map(x=>x.spots));
  const rows=(d.tenants||[]).map(x=>
    `<tr>
      <td>${esc(x.guild)}</td>
      <td class="num">${x.spots}</td>
      <td class="bar-cell" aria-hidden="true"><div class="bar-bg"><div class="bar-fill" style="width:${Math.round(x.spots/maxSpots*100)}%"></div></div></td>
      <td class="num">${x.votes}</td>
      <td class="num">${x.nights}</td>
      <td>${esc(x.last)||'—'}</td>
    </tr>`).join('');
  document.getElementById('tenants').innerHTML=
    `<tr><th scope="col">server / guild id</th><th scope="col" class="num">spots</th><th class="bar-cell" aria-hidden="true"></th><th scope="col" class="num">votes</th><th scope="col" class="num">nights</th><th scope="col">last capture</th></tr>`
    +(rows||`<tr><td colspan="6" class="empty">no catalogs yet</td></tr>`);

  const secs=v=>v==null?'—':(v>=90?(v/60).toFixed(1)+'m':v.toFixed(1)+'s');

  // #18 time to card — paste → card as the person pasting sees it.
  const tc=d.time_to_card;
  if(!tc){
    document.getElementById('ttc').innerHTML=`<div class="empty">not logged (TIME_TO_CARD_LOG=off)</div>`;
  }else{
    const day=tc.last_24h||{}, all=tc.all||{};
    document.getElementById('ttc').innerHTML=`
      <div class="tile"><b>${secs(day.median_s)}</b><span>median, last 24 h</span></div>
      <div class="tile"><b>${secs(day.p95_s)}</b><span>p95, last 24 h</span></div>
      <div class="tile"><b>${day.cards??0} / ${day.pastes??0}</b><span>pastes that became cards</span></div>
      <div class="tile"><b>${secs(all.median_s)}</b><span>median, all time</span></div>`;
  }

  // #29 capture health — counts and seconds from the timing log, never links.
  const h=d.capture_health;
  if(!h){
    document.getElementById('health').innerHTML=`<div class="empty">timing log is off (CAPTURE_LOG=off)</div>`;
    document.getElementById('stages').innerHTML='';
  }else{
    const ov=h.over_threshold||{}, du=h.duplicates||{}, st=h.states||{};
    document.getElementById('health').innerHTML=`
      <div class="tile"><b>${h.captures}</b><span>captures logged</span></div>
      <div class="tile"><b>${secs(h.duration_s.median)}</b><span>median time</span></div>
      <div class="tile"><b>${secs(h.duration_s.p95)}</b><span>p95 time</span></div>
      <div class="tile"><b>${ov['180s']??0}</b><span>past 3 min</span></div>
      <div class="tile"><b>${ov['300s']??0}</b><span>past 5 min</span></div>
      <div class="tile"><b>${du.wasted_captures??0}</b><span>duplicate captures</span></div>
      <div class="tile"><b>${st.failed??0}</b><span>failed</span></div>`;
    const stages=Object.entries(h.stage_median_s||{});
    document.getElementById('stages').innerHTML=
      `<tr><th scope="col">stage</th><th scope="col" class="num">median time</th></tr>`
      +(stages.map(([k,v])=>`<tr><td>${esc(k)}</td><td class="num">${secs(v)}</td></tr>`).join('')
        ||`<tr><td colspan="2" class="empty">no captures logged yet</td></tr>`);
  }

  const q=d.queue||{}, L=d.limits||{};
  const span=s=>s>=3600?Math.round(s/3600)+'h':Math.round(s/60)+'m';
  const lim=(name,x)=>!x||!x.limit?`${name}: off`:`${name}: ${x.limit} per ${span(x.window_s)}`;
  const qchip=(ok,text)=>`<span class="chip ${ok?'on':'off'}"><span class="dot" aria-hidden="true"></span>${text}<span class="sr">${ok?'':' — needs a look'}</span></span>`;
  document.getElementById('queue').innerHTML=[
    qchip((q.waiting??0)<(q.max_queued??1), `waiting ${q.waiting??0} / ${q.max_queued??'?'}`),
    qchip(true, `running ${q.running??0} of ${q.workers??1} worker${q.workers===1?'':'s'}`),
    qchip(!(q.failed_recent), `failed (last ${span(q.recent_window_s??3600)}) ${q.failed_recent??0}`),
    qchip(!!q.durable, q.durable?'job store: durable':'job store: in-memory'),
    qchip(true, `retries: ${q.retries_allowed??0}`),
    qchip(true, lim('per user',L.user)),
    qchip(true, lim('per server',L.server)),
    qchip(true, lim('daily',L.daily)),
  ].join('');

  document.getElementById('feed').innerHTML=(d.captures||[]).map(c=>{
    const when=new Date(c.ts*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
    const fails=(c.rejected||0)+(c.unreadable||0);
    return `<div class="cap"><time>${when}</time><span class="g">${esc(c.guild_id)||'default'}</span>
      <span>${c.urls} link${c.urls===1?'':'s'} → <span class="ok-n">${c.added} added</span>${fails?` · <span class="bad-n">${fails} failed</span>`:''}</span>
      <span class="r">${(c.duration_s??0).toFixed(1)}s</span></div>`;
  }).join('')||`<div class="empty">no captures since the app started</div>`;

  startBar();
}

window.addEventListener('DOMContentLoaded',()=>{
  countEl=document.getElementById('next');
  pgEl=document.getElementById('pgbar');
  pulseEl=document.getElementById('pulse');
  load();
  setInterval(load, 5000);
  setInterval(()=>{
    countdown=(countdown<=1)?5:countdown-1;
    countEl.textContent=`next refresh in ${countdown}s`;
  }, 1000);
});
</script>
</body></html>"""
