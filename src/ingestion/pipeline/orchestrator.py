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

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Optional, TYPE_CHECKING

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
        geo_enricher: Optional[GeoEnricher] = None,
        temporal_resolver: Optional[TemporalResolver] = None,
        jsonl_sink: Optional[JsonlSink] = None,
        chroma_sink: Optional["ChromaSink"] = None,
        on_result: Optional[Callable[[IngestionResult], None]] = None,
    ):
        self.settings = settings
        self.extractor = extractor
        self.geo_enricher = geo_enricher
        self.temporal_resolver = temporal_resolver
        self.jsonl_sink = jsonl_sink
        self.chroma_sink = chroma_sink
        self.on_result = on_result

    async def run(self, urls: list[str]) -> "RunReport":
        started_at = datetime.now(timezone.utc)
        t0 = perf_counter()
        results: list[IngestionResult] = []

        async with SocialSessionManager(self.settings) as session:
            for url in urls:
                try:
                    snapshot = await session.fetch(url)
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
        record, reason = build_record(raw, extractor=self.extractor)
        if record is None:
            return IngestionResult(
                url=snapshot.url,
                fetch_status=snapshot.status,
                rejection_reason=reason,
                raw_ref=self._save_raw(snapshot),
            )

        if self.geo_enricher is not None:           # Phase 3
            record = self.geo_enricher.enrich(record)

        if self.temporal_resolver is not None:      # Phase 4
            record, expired = self.temporal_resolver.resolve(record)
            if expired is not None:
                return IngestionResult(
                    url=snapshot.url,
                    fetch_status=snapshot.status,
                    rejection_reason=expired,
                    raw_ref=self._save_raw(snapshot),
                )

        return IngestionResult(url=snapshot.url, fetch_status=snapshot.status, record=record)

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
