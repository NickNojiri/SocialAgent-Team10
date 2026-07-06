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
from src.ingestion.schemas.results import FetchStatus, IngestionResult
from src.ingestion.schemas.snapshot import PageSnapshot
from src.ingestion.sinks.jsonl_sink import JsonlSink

if TYPE_CHECKING:
    from src.ingestion.sinks.chroma_sink import ChromaSink
    from src.ingestion.pipeline.transcriber import Transcriber

log = logging.getLogger("ingestion.orchestrator")

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
        on_result: Optional[Callable[[IngestionResult], None]] = None,
    ):
        self.settings = settings
        self.extractor = extractor
        self.transcriber = transcriber
        self.summarizer = summarizer
        self.geo_enricher = geo_enricher
        self.temporal_resolver = temporal_resolver
        self.jsonl_sink = jsonl_sink
        self.chroma_sink = chroma_sink
        # Optional authenticated fetch source (AuthedInstagramSource): for IG post
        # URLs it returns a RawPostSnapshot directly (the 95-99% path), and the
        # Playwright browser is only launched for URLs it can't handle.
        self.authed_source = authed_source
        self.on_result = on_result

    async def run(self, urls: list[str]) -> "RunReport":
        started_at = datetime.now(timezone.utc)
        t0 = perf_counter()
        results: list[IngestionResult] = []

        # The browser is opened lazily — a run where every URL is served by the
        # authed source never launches Chromium at all.
        session: Optional[SocialSessionManager] = None
        try:
            for url in urls:
                try:
                    raw = None
                    if self.authed_source is not None:
                        # Sync instagrapi call — offload so it doesn't block the loop.
                        raw = await asyncio.to_thread(self.authed_source.fetch_url, url)

                    if raw is not None:
                        result = self._process_raw(raw, url, FetchStatus.OK)
                    else:
                        if session is None:
                            session = await SocialSessionManager(self.settings).__aenter__()
                        snapshot = await session.fetch(url)
                        # If IG returned a login wall, attempt the embed-page fallback
                        # before giving up — recovers the caption without login ~70% of the time.
                        if snapshot.status is FetchStatus.LOGIN_WALL:
                            recovered = await try_embed_fallback(url)
                            if recovered is not None:
                                snapshot = recovered
                        result = self._process(snapshot)
                except Exception as exc:  # last-ditch guard: one URL never kills the run
                    log.exception(f"[pipeline] unexpected error on {url}: {exc}")
                    result = IngestionResult(
                        url=url,
                        fetch_status=FetchStatus.ERROR,
                        rejection_reason=f"unexpected error: {type(exc).__name__}: {exc}",
                    )

                if self.jsonl_sink is not None:
                    self.jsonl_sink.write(result)          # JSONL is the source of truth
                if self.chroma_sink is not None:
                    self.chroma_sink.write(result)         # opt-in vector store, best-effort
                results.append(result)
                if self.on_result is not None:
                    self.on_result(result)
        finally:
            if session is not None:
                await session.__aexit__(None, None, None)

        chroma_count = self.chroma_sink.count() if self.chroma_sink is not None else None
        return RunReport(
            results=results,
            started_at=started_at,
            duration_s=perf_counter() - t0,
            out_path=getattr(self.jsonl_sink, "path", None),
            chroma_count=chroma_count,
            chroma_collection=self.settings.chroma_collection if chroma_count is not None else None,
        )

    # ── per-URL stages ───────────────────────────────────────────────────────

    def _process(self, snapshot: PageSnapshot) -> IngestionResult:
        if snapshot.status is not FetchStatus.OK:
            return IngestionResult(
                url=snapshot.url,
                fetch_status=snapshot.status,
                raw_ref=self._save_raw(snapshot),
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
        if self.transcriber is not None and getattr(raw, "video_url", None):
            transcript = self.transcriber.transcribe_url(raw.video_url)
            if transcript:
                raw.transcript = transcript

        record, reason = build_record(raw, extractor=self.extractor)
        if record is None:
            return IngestionResult(url=url, fetch_status=fetch_status, rejection_reason=reason)

        # Phase 2.5: a user-facing "quick description" from the audio. Only when we
        # actually transcribed something — no transcript → no summary → card shows "No info".
        if self.summarizer is not None and getattr(raw, "transcript", None):
            try:
                record.summary = self.summarizer(getattr(raw, "caption", None), raw.transcript)
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
