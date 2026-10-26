"""End-to-end ingestion orchestration + diagnostic reporting (Phase 7).

Binds the whole cycle into one manager:

    URL → Playwright fetch → (LLM extract) → (geo enrich) → (temporal resolve)
        → JSONL sink (+ optional Chroma sink)

Each stage is optional and degrades independently (Ollama down → heuristics,
Nominatim blocked → unresolved geo, etc.), so a single URL — or a whole missing
subsystem — never aborts the run. Every URL yields exactly one IngestionResult;
the RunReport aggregates them into an honest summary of successes, validation
rejections, and connectivity/access exceptions (timeouts, login walls, …).
"""

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional, TYPE_CHECKING

from src.ingestion.browser.ig_embed import try_embed_fallback
from src.ingestion.browser.session_manager import SocialSessionManager
from src.ingestion.config import IngestionSettings
from src.ingestion.extractors.base import select_extractor
from src.ingestion.pipeline.geo_enricher import GeoEnricher
from src.ingestion.pipeline.llm_extractor import LlmFieldExtractor
from src.ingestion.pipeline.temporal_resolver import TemporalResolver
from src.ingestion.pipeline.validator import build_record
from src.ingestion.schemas.results import FetchStatus, IngestionResult, PostGist
from src.ingestion.schemas.snapshot import PageSnapshot
from src.ingestion.sinks.jsonl_sink import JsonlSink

if TYPE_CHECKING:
    from src.ingestion.sinks.chroma_sink import ChromaSink
    from src.ingestion.pipeline.ocr import ImageTextReader
    from src.ingestion.pipeline.transcriber import Transcriber

log = logging.getLogger("ingestion.orchestrator")


def _ollama_reachable(settings: IngestionSettings) -> bool:
    """2-second pre-flight so a down/hung Ollama costs one probe per run, not a
    90s timeout per stage per URL. Import-light on purpose."""
    import httpx

    try:
        httpx.get(f"{settings.ollama_url}/api/tags", timeout=2.0)
        return True
    except Exception:
        return False

# Fetch outcomes that mean "we couldn't read the page" (the connectivity/access bucket).
_CONNECTIVITY_STATUSES = {
    FetchStatus.LOGIN_WALL,
    FetchStatus.CONSENT_WALL,
    FetchStatus.NOT_FOUND,
    FetchStatus.TIMEOUT,
    FetchStatus.ERROR,
}


class IngestionPipeline:
    """The overarching orchestration manager. Hold the (optional) stage components
    and run the full cycle over a list of URLs, producing a RunReport."""

    def __init__(
        self,
        settings: IngestionSettings,
        *,
        extractor: Optional[LlmFieldExtractor] = None,
        transcriber: Optional["Transcriber"] = None,
        summarizer: Optional[Callable[[Optional[str], Optional[str]], Optional[str]]] = None,
        geo_enricher: Optional[GeoEnricher] = None,
        temporal_resolver: Optional[TemporalResolver] = None,
        jsonl_sink: Optional[JsonlSink] = None,
        chroma_sink: Optional["ChromaSink"] = None,
        authed_source: Optional[Any] = None,
        ocr_reader: Optional["ImageTextReader"] = None,
        on_result: Optional[Callable[[IngestionResult], None]] = None,
        on_stage: Optional[Callable[[str, str], None]] = None,
        frame_describer: Optional[Callable[[list[bytes]], Optional[str]]] = None,
        post_summarizer: Optional[Callable[..., Optional[dict]]] = None,
    ):
        self.settings = settings
        self.extractor = extractor
        self.transcriber = transcriber
        # summarizer(caption, transcript) or, when it takes a third argument, (…, visual).
        self.summarizer = summarizer
        # frame_describer(frames) → what the video shows; post_summarizer(caption, transcript,
        # visual, author) → {title, kind, summary} for a reel that isn't a place. Both optional.
        self.frame_describer = frame_describer
        self.post_summarizer = post_summarizer
        self.ocr_reader = ocr_reader
        # Flipped to False by the per-run Ollama pre-flight; direct _process
        # calls (tests, CLI internals) keep LLM stages enabled by default.
        self._llm_ok = True
        self.geo_enricher = geo_enricher
        self.temporal_resolver = temporal_resolver
        self.jsonl_sink = jsonl_sink
        self.chroma_sink = chroma_sink
        # Optional authenticated fetch source (AuthedInstagramSource): for IG post
        # URLs it returns a RawPostSnapshot directly (the 95-99% path), and the
        # Playwright browser is only launched for URLs it can't handle.
        self.authed_source = authed_source
        self._authed_misses = 0                  # reset at the start of every run()
        self.on_result = on_result
        # Progress hook: (url, stage) at fetching / transcribing / extracting /
        # saving / done. Drives the bot's live status line via the job queue
        # (serving/jobs.py). Best-effort — a failing callback never fails a run.
        self.on_stage = on_stage

    def _stage(self, url: str, stage: str) -> None:
        if self.on_stage is None:
            return
        try:
            self.on_stage(url, stage)
        except Exception as exc:
            log.debug(f"[pipeline] on_stage callback failed: {exc}")

    async def run(self, urls: list[str]) -> "RunReport":
        started_at = datetime.now(timezone.utc)
        t0 = perf_counter()
        results: list[IngestionResult] = []

        # Fail-fast pre-flight: one 2s probe decides the whole run's LLM usage,
        # so a down Ollama degrades to heuristics instantly instead of hitting
        # a 90s timeout on every URL.
        self._llm_ok = True
        if self.extractor is not None or self.summarizer is not None or self.post_summarizer is not None:
            if not _ollama_reachable(self.settings):
                self._llm_ok = False
                log.warning(
                    "[fail-fast] Ollama unreachable — running heuristics-only "
                    "(no LLM extraction or audio summaries this run)"
                )

        budget = self.settings.capture_budget_s
        self._authed_misses = 0                  # circuit breaker, per run (see _capture)
        browser = _LazyBrowser(self.settings)    # Chromium only if a URL needs it
        try:
            for url in urls:
                self._stage(url, "fetching")
                path = "none"
                try:
                    capture = await self._capture(url, browser)
                    path = capture.path
                    # Post-fetch stages run in a thread under a hard budget: a hung
                    # backend fails this one URL with a clear reason. (The worker
                    # thread can outlive the timeout — logged, accepted.)
                    if capture.raw is not None:
                        work = asyncio.to_thread(self._process_raw, capture.raw, url, FetchStatus.OK)
                    else:
                        work = asyncio.to_thread(self._process, capture.snapshot)
                    result = await asyncio.wait_for(work, timeout=budget)
                except asyncio.TimeoutError:
                    log.error(f"[fail-fast] {url} exceeded the {budget:.0f}s capture budget")
                    result = IngestionResult(
                        url=url,
                        fetch_status=FetchStatus.ERROR,
                        rejection_reason=f"capture exceeded the {budget:.0f}s budget",
                    )
                except Exception as exc:  # last-ditch guard: one URL never kills the run
                    log.exception(f"[pipeline] unexpected error on {url}: {exc}")
                    result = IngestionResult(
                        url=url,
                        fetch_status=FetchStatus.ERROR,
                        rejection_reason=f"unexpected error: {type(exc).__name__}: {exc}",
                    )
                result.capture_path = path

                self._stage(url, "saving")
                if self.jsonl_sink is not None:
                    self.jsonl_sink.write(result)          # JSONL is the source of truth
                if self.chroma_sink is not None:
                    self.chroma_sink.write(result)         # opt-in vector store, best-effort
                results.append(result)
                if self.on_result is not None:
                    self.on_result(result)
                self._stage(url, "done")
        finally:
            await browser.close()

        chroma_count = self.chroma_sink.count() if self.chroma_sink is not None else None
        return RunReport(
            results=results,
            started_at=started_at,
            duration_s=perf_counter() - t0,
            out_path=getattr(self.jsonl_sink, "path", None),
            chroma_count=chroma_count,
            chroma_collection=self.settings.chroma_collection if chroma_count is not None else None,
        )

    # ── capture: the fallback chain ──────────────────────────────────────────

    async def _capture(self, url: str, browser: "_LazyBrowser") -> "_Capture":
        """Fetch one URL down an explicit chain, cheapest and richest first, and
        record which rung served it (``IngestionResult.capture_path``):

          1. ``authed``  — the logged-in source: caption, location tag, coords.
          2. ``browser`` — the public page in Chromium.
          3. ``embed``   — the /embed/ page over plain HTTP, when the browser hits a
                           login wall or times out (~70% caption recovery).

        A new rung (the official oEmbed API, docs/handoffs/TRACK-A-COMPLIANT-CAPTURE.md)
        slots in here as one more step. The authed rung has a per-run circuit breaker:
        after ``authed_max_misses`` Instagram URLs in a row come back empty or raise,
        it is skipped for the rest of the run, so a flagged account isn't hit on every
        URL (the 2026-10-09 incident). Its own sticky login guard stays as it is."""
        if self.authed_source is not None and self._authed_misses < self.settings.authed_max_misses:
            try:
                # Sync instagrapi call — offload so it doesn't block the loop.
                raw = await asyncio.to_thread(self.authed_source.fetch_url, url)
            except Exception as exc:                # a source bug degrades, never fails the URL
                log.warning(f"[capture] authed source raised on {url}: {type(exc).__name__}: {exc}")
                raw = None
            if raw is not None:
                self._authed_misses = 0
                # A source may name its rung (the DM-share store says "dm_share", ADR-0007).
                return _Capture(raw=raw, path=getattr(raw, "capture_path", None) or "authed")
            # A non-IG URL is a pass, not a miss; so is a source that only serves what it
            # was handed (the DM-share store), since it has no account to get flagged.
            if "instagram.com/" in url and getattr(self.authed_source, "counts_misses", True):
                self._authed_misses += 1
                if self._authed_misses == self.settings.authed_max_misses:
                    log.warning(
                        f"[capture] authed source returned nothing for {self._authed_misses} Instagram "
                        "URLs in a row — skipping it for the rest of this run (account flagged or "
                        "rate-limited?); using the public path"
                    )

        snapshot = await (await browser.get()).fetch(url)
        if snapshot.status not in (FetchStatus.LOGIN_WALL, FetchStatus.TIMEOUT):
            return _Capture(snapshot=snapshot, path="browser")
        log.info("[capture] %s → %s; trying embed fallback", url, snapshot.status.value)
        recovered = await try_embed_fallback(url)
        if recovered is not None:
            return _Capture(snapshot=recovered, path="embed")
        log.info("[capture] embed fallback also failed for %s", url)
        return _Capture(snapshot=snapshot, path="none")

    # ── per-URL stages ───────────────────────────────────────────────────────

    def _process(self, snapshot: PageSnapshot) -> IngestionResult:
        if snapshot.status is not FetchStatus.OK:
            return IngestionResult(
                url=snapshot.url,
                fetch_status=snapshot.status,
                raw_ref=self._save_raw(snapshot),
                # PageSnapshot allows extra fields; the fetcher puts its error
                # text there. Keep it — the retry policy needs it (feature #26).
                fetch_error=getattr(snapshot, "error", None),
            )

        page_extractor = select_extractor(snapshot.final_url or snapshot.url)
        raw = page_extractor.extract(snapshot)

        # The reel mp4 lives in og:video, which IG serves even behind the login
        # overlay; hand it to the shared tail so _process_raw can transcribe it.
        if not raw.video_url:
            from src.ingestion.pipeline.transcriber import video_url_from_html, video_url_from_meta

            raw.video_url = video_url_from_meta(snapshot.meta) or video_url_from_html(snapshot.html)

        result = self._process_raw(raw, snapshot.url, snapshot.status)
        # Persist the HTML of rejected fetches so they stay debuggable.
        if result.record is None and result.raw_ref is None:
            result.raw_ref = self._save_raw(snapshot)
        return result

    def _process_raw(self, raw, url: str, fetch_status: FetchStatus) -> IngestionResult:
        """Shared post-fetch stages, from either the Playwright path or the authed
        source. `raw` is a RawPostSnapshot; everything below is fetch-agnostic."""
        # Phase 2.5: transcribe the reel's audio so a venue/location that is only
        # *spoken* (never written in the caption) still reaches the LLM.
        visual = None
        if self.transcriber is not None and getattr(raw, "video_url", None):
            self._stage(url, "transcribing")
            frames_wanted = self.settings.video_frames if (self.frame_describer and self._llm_ok) else 0
            if frames_wanted and hasattr(self.transcriber, "transcribe_media"):
                seen: dict = {}

                def describe(frames: list[bytes]) -> None:   # runs while Whisper transcribes
                    try:
                        seen["visual"] = self.frame_describer(frames)
                    except Exception as exc:
                        seen["visual"] = None
                        log.warning(f"[frames] description skipped: {type(exc).__name__}: {exc}")

                transcript, frames = self.transcriber.transcribe_media(
                    raw.video_url, frames=frames_wanted, on_frames=describe)
                if frames and "visual" not in seen:           # a transcriber that didn't run it
                    describe(frames)
                visual = seen.get("visual")
            else:
                transcript = self.transcriber.transcribe_url(raw.video_url)
            if transcript:
                raw.transcript = transcript

        # Phase 2.6: read burned-in on-screen text from the cover image — info
        # that is *typed over the video*, never spoken and never in the caption.
        if self.ocr_reader is not None and not getattr(raw, "frame_text", None):
            frame_text = self.ocr_reader.read_url(getattr(raw, "image_url", None))
            if frame_text:
                raw.frame_text = frame_text

        self._stage(url, "extracting")
        record, reason = build_record(raw, extractor=self.extractor if self._llm_ok else None)
        if record is None:
            result = IngestionResult(url=url, fetch_status=fetch_status, rejection_reason=reason)
            # Not a spot, but the reel was read: say what it's about anyway.
            if self.post_summarizer is not None and self._llm_ok:
                author = getattr(raw, "author_handle", None)
                try:
                    out = self.post_summarizer(
                        getattr(raw, "caption", None), getattr(raw, "transcript", None), visual, author)
                    if out:
                        result.gist = PostGist(**out, author=author, image_url=getattr(raw, "image_url", None))
                except Exception as exc:
                    log.warning(f"[post summary] skipped: {type(exc).__name__}: {exc}")
            return result

        # Phase 2.5: a user-facing "quick description" from the audio (and what the video
        # shows, when frames were read). Nothing heard or seen → no summary → "No info".
        transcript = getattr(raw, "transcript", None)
        if self.summarizer is not None and self._llm_ok and (transcript or visual):
            try:
                caption = getattr(raw, "caption", None)
                record.summary = (self.summarizer(caption, transcript, visual) if visual
                                  else self.summarizer(caption, transcript))
            except Exception as exc:
                log.warning(f"[summary] skipped: {type(exc).__name__}: {exc}")

        if self.geo_enricher is not None:           # Phase 3
            record = self.geo_enricher.enrich(record)

        if self.temporal_resolver is not None:      # Phase 4
            record, expired = self.temporal_resolver.resolve(record)
            if expired is not None:
                return IngestionResult(url=url, fetch_status=fetch_status, rejection_reason=expired)

        return IngestionResult(url=url, fetch_status=fetch_status, record=record)

    def _save_raw(self, snapshot: PageSnapshot) -> Optional[Path]:
        """Persist the HTML of failed/rejected fetches so they're debuggable."""
        if not snapshot.html:
            return None
        raw_dir = Path(self.settings.raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        path = raw_dir / f"snapshot-{stamp}.html"
        path.write_text(snapshot.html, encoding="utf-8")
        return path


# ── capture helpers ──────────────────────────────────────────────────────────


@dataclass
class _Capture:
    """What the chain produced for one URL: a RawPostSnapshot from the authed source,
    or a PageSnapshot from the browser/embed rungs, plus the rung that served it."""
    raw: Any = None
    snapshot: Optional[PageSnapshot] = None
    path: str = "none"


class _LazyBrowser:
    """Opens Chromium on first use only — a run fully served by the authed source
    never launches it — and closes it once at the end of the run."""

    def __init__(self, settings: IngestionSettings):
        self._settings = settings
        self._session: Optional[SocialSessionManager] = None

    async def get(self) -> SocialSessionManager:
        if self._session is None:
            self._session = await SocialSessionManager(self._settings).__aenter__()
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None


# ── diagnostics ──────────────────────────────────────────────────────────────


@dataclass
class RunReport:
    results: list[IngestionResult]
    started_at: datetime
    duration_s: float
    out_path: Optional[Path] = None
    chroma_count: Optional[int] = None
    chroma_collection: Optional[str] = None

    @property
    def validated(self) -> list[IngestionResult]:
        return [r for r in self.results if r.record is not None]

    @property
    def rejected(self) -> list[IngestionResult]:
        """Page read OK, but the record failed validation or was expired."""
        return [r for r in self.results if r.record is None and r.rejection_reason]

    @property
    def connectivity_failures(self) -> list[IngestionResult]:
        """Couldn't read the page — login walls, timeouts, 404s, errors."""
        return [
            r for r in self.results
            if r.record is None and not r.rejection_reason
            and r.fetch_status in _CONNECTIVITY_STATUSES
        ]

    def _counter(self, values) -> Counter:
        return Counter(values)

    def to_dict(self) -> dict:
        fetch_counts = self._counter(r.fetch_status.value for r in self.results)
        return {
            "started_at": self.started_at.isoformat(),
            "duration_s": self.duration_s,
            "out_path": str(self.out_path) if self.out_path is not None else None,
            "chroma_count": self.chroma_count,
            "counts": {
                "processed": len(self.results),
                "validated": len(self.validated),
                "rejected": len(self.rejected),
                "unreadable": len(self.connectivity_failures),
            },
            "fetch_outcomes": dict(fetch_counts),
            "capture_paths": dict(self._counter(r.capture_path or "none" for r in self.results)),
            "rejections": [
                {"url": r.url, "reason": r.rejection_reason}
                for r in self.rejected
            ],
            "connectivity_failures": [
                {"url": r.url, "status": r.fetch_status.value}
                for r in self.connectivity_failures
            ],
            "results": [
                {
                    "url": r.url,
                    "fetch_status": r.fetch_status.value,
                    "validated": r.record is not None,
                    "rejection_reason": r.rejection_reason,
                }
                for r in self.results
            ],
        }

    def render(self) -> str:
        lines: list[str] = []
        bar = "═" * 64
        lines.append(bar)
        lines.append(" INGESTION RUN REPORT")
        lines.append(bar)
        lines.append(
            f" URLs processed : {len(self.results):<4}   duration: {self.duration_s:.1f}s"
            f"   started: {self.started_at.isoformat(timespec='seconds')}"
        )
        dest = f" → {self.out_path}" if self.out_path else ""
        lines.append(f" ✅ validated    : {len(self.validated)}{dest}")
        if self.chroma_count is not None:
            lines.append(f" 🧠 vector store : {self.chroma_count} in '{self.chroma_collection}'")
        lines.append(f" ⚠️  rejected     : {len(self.rejected)}")
        lines.append(f" 🚫 unreadable   : {len(self.connectivity_failures)}")

        # Fetch outcomes
        fetch_counts = self._counter(r.fetch_status.value for r in self.results)
        if fetch_counts:
            lines.append("")
            lines.append(" Fetch outcomes:")
            for status, n in fetch_counts.most_common():
                lines.append(f"   {status:<14} {n}")

        # Which capture rung served each URL (authed / browser / embed / none)
        paths = self._counter(r.capture_path or "none" for r in self.results)
        if paths:
            lines.append(" Capture paths:  " + ", ".join(f"{k} {v}" for k, v in paths.most_common()))

        # Validation rejections (the "why was data dropped" view)
        if self.rejected:
            lines.append("")
            lines.append(" Validation rejections:")
            for reason, n in self._counter(r.rejection_reason for r in self.rejected).most_common():
                lines.append(f"   {n} × {reason}")

        # Connectivity / access exceptions (timeouts, login walls, …)
        if self.connectivity_failures:
            lines.append("")
            lines.append(" Connectivity / access issues:")
            for r in self.connectivity_failures:
                lines.append(f"   {r.fetch_status.value:<12} {r.url}")

        # What landed in the store
        if self.validated:
            lines.append("")
            lines.append(" Records written:")
            geo = self._counter(r.record.geo.resolution.value for r in self.validated)
            lines.append("   geo:      " + ", ".join(f"{k} {v}" for k, v in geo.most_common()))
            sched = self._counter(
                r.record.schedule.status.value
                for r in self.validated if r.record.schedule is not None
            )
            if sched:
                lines.append("   schedule: " + ", ".join(f"{k} {v}" for k, v in sched.most_common()))

        lines.append(bar)
        return "\n".join(lines)


def result_line(result: IngestionResult) -> str:
    """One-line live progress string for a single URL outcome."""
    if result.record is not None:
        record = result.record
        geo = record.geo
        coords = f" ({geo.lat:.4f},{geo.lng:.4f})" if geo.lat is not None else ""
        if record.schedule is None:
            sched = ""
        elif record.schedule.start_utc is not None:
            sched = f" | time={record.schedule.status.value} {record.schedule.start_utc.isoformat()}"
        else:
            sched = f" | time={record.schedule.status.value}"
        return (
            f"[ok]      {result.url} -> {record.venue_name!r} / {record.category.value} "
            f"| geo={geo.resolution.value}{coords} conf={geo.confidence}{sched}"
        )
    if result.rejection_reason:
        return f"[reject]  {result.url} -> {result.rejection_reason}"
    return f"[{result.fetch_status.value}] {result.url}"
