"""Measure live Instagram capture reliability on a reproducible random sample.

Run from the repository root on a machine allowed to contact Instagram:

    .venv/bin/python scripts/capture_harness.py \
        --source fixtures/labels.jsonl \
        --sample-size 50 \
        --seed 20261017 \
        --output docs/results/capture-harness-2026-10-17.json

The harness intentionally measures the public, logged-out capture baseline. It
uses the existing browser/embed fetch, Instagram extractor, normalizer, and
validator. It does not use credentials, Ollama, transcription, OCR, geocoding,
temporal resolution, JSONL/Chroma sinks, or raw-page snapshots. The JSON report
is its only persistent output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO / "docs" / "results"
sys.path.insert(0, str(REPO))

from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline.orchestrator import IngestionPipeline  # noqa: E402
from src.ingestion.schemas.results import FetchStatus, IngestionResult  # noqa: E402
from src.ingestion.schemas.snapshot import PageSnapshot  # noqa: E402

FAILURE_CLASSES = (
    "login_wall",
    "no_video",
    "timeout",
    "no_venue",
    "not_a_place",
    "other",
)

_REEL_PATH = re.compile(r"^/(?:reel|reels)/(?P<code>[A-Za-z0-9_-]+)/?$")
_NO_TEXT = "no human-readable text was extracted from the page"
_NOT_A_PLACE = "post is not clearly about a place, event, or food"
_CAPTURE_TIMEOUT = "capture exceeded"


@dataclass(frozen=True)
class HarnessRow:
    """The dashboard-safe measurement for one URL."""

    url: str
    outcome: str
    elapsed_seconds: float
    failure_class: Optional[str]
    fetch_status: str
    video_available: Optional[bool]

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "outcome": self.outcome,
            "elapsed_seconds": self.elapsed_seconds,
            "failure_class": self.failure_class,
            "fetch_status": self.fetch_status,
            "video_available": self.video_available,
        }


def canonical_reel_url(value: object) -> Optional[str]:
    """Return one canonical public Instagram reel URL, or None."""
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    if parsed.scheme != "https" or host != "instagram.com":
        return None
    match = _REEL_PATH.fullmatch(parsed.path)
    if match is None:
        return None
    return f"https://www.instagram.com/reel/{match.group('code')}/"


def load_eligible_urls(source: Path) -> list[str]:
    """Read only URL fields from a UTF-8 label corpus and return unique reels."""
    urls: set[str] = set()
    text = source.read_text(encoding="utf-8")
    # Do not use splitlines(): captions in this corpus can contain U+2028/U+2029.
    for line_number, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{source}:{line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{source}:{line_number}: expected a JSON object")
        url = canonical_reel_url(row.get("url"))
        if url is not None:
            urls.add(url)
    return sorted(urls)


def select_urls(urls: list[str], sample_size: int, seed: int) -> list[str]:
    """Choose a deterministic sample without inspecting any label fields."""
    if sample_size < 1:
        raise ValueError("sample size must be at least 1")
    pool = sorted(set(urls))
    if len(pool) < sample_size:
        raise ValueError(
            f"requested {sample_size} URLs, but the source has only {len(pool)} eligible reels"
        )
    return random.Random(seed).sample(pool, sample_size)


def failure_class(
    result: IngestionResult, *, video_available: Optional[bool]
) -> Optional[str]:
    """Map the repository's real result fields onto Track A's six classes."""
    if result.record is not None:
        return None

    status = result.fetch_status
    reason = result.rejection_reason or ""
    if status in (FetchStatus.LOGIN_WALL, FetchStatus.CONSENT_WALL):
        return "login_wall"
    if status is FetchStatus.TIMEOUT or reason.startswith(_CAPTURE_TIMEOUT):
        return "timeout"
    if status is FetchStatus.OK and reason.startswith(_NOT_A_PLACE):
        return "not_a_place"
    if status is FetchStatus.OK and reason.startswith(_NO_TEXT):
        return "no_video" if video_available is False else "other"
    if status is FetchStatus.OK and any(
        issue.strip().startswith("venue_name:") for issue in reason.split(";")
    ):
        return "no_venue"
    return "other"


def result_outcome(result: IngestionResult) -> str:
    """Separate unreadable pages from content and processing rejections."""
    if result.record is not None:
        return "success"
    if result.fetch_status is FetchStatus.OK:
        return "content_rejected"
    if result.rejection_reason:
        return "processing_failed"
    return "fetch_failed"


class CaptureHarnessPipeline(IngestionPipeline):
    """The existing pipeline with observation on and persistence turned off."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.video_available: dict[str, bool] = {}

    def _process_raw(
        self, raw: Any, url: str, fetch_status: FetchStatus
    ) -> IngestionResult:
        self.video_available[url] = bool(getattr(raw, "video_url", None))
        return super()._process_raw(raw, url, fetch_status)

    def _save_raw(self, snapshot: PageSnapshot) -> None:
        # The production pipeline keeps failed HTML for debugging. A measurement
        # run must leave only its agreed JSON report, so the harness suppresses it.
        return None


def harness_settings(*, headed: bool = False) -> IngestionSettings:
    """Settings for the narrow public-fetch measurement, with extras disabled."""
    return IngestionSettings(
        headless=not headed,
        allow_file_urls=False,
        llm_enabled=False,
        transcribe_enabled=False,
        ocr_enabled=False,
        geocode_enabled=False,
        temporal_enabled=False,
        chroma_enabled=False,
        ig_username="",
        ig_password="",
    )


def build_harness_pipeline(
    settings: IngestionSettings,
    *,
    on_result: Callable[[IngestionResult], None],
    on_stage: Callable[[str, str], None],
) -> CaptureHarnessPipeline:
    """Build a pipeline that cannot write captures, media, sessions, or snapshots."""
    return CaptureHarnessPipeline(
        settings,
        extractor=None,          # no Ollama request
        transcriber=None,        # no video download or temporary media file
        summarizer=None,
        geo_enricher=None,       # no OpenStreetMap request
        temporal_resolver=None,
        jsonl_sink=None,         # cannot append data/inspirations.jsonl
        chroma_sink=None,        # cannot create or update ChromaDB
        authed_source=None,      # no credentials or session-cookie file
        ocr_reader=None,         # no image download
        on_result=on_result,
        on_stage=on_stage,
    )


async def run_capture(
    urls: list[str],
    *,
    settings: Optional[IngestionSettings] = None,
    pipeline_factory: Callable[..., CaptureHarnessPipeline] = build_harness_pipeline,
    clock: Callable[[], float] = perf_counter,
) -> list[HarnessRow]:
    """Run one sequential batch and return exactly one measurement per URL."""
    starts: dict[str, float] = {}
    elapsed: dict[str, float] = {}

    def on_stage(url: str, stage: str) -> None:
        if stage == "fetching":
            starts[url] = clock()

    def on_result(result: IngestionResult) -> None:
        started = starts.get(result.url)
        if started is not None:
            elapsed[result.url] = max(0.0, clock() - started)

    pipeline = pipeline_factory(
        settings or harness_settings(), on_result=on_result, on_stage=on_stage
    )
    report = await pipeline.run(urls)

    if [result.url for result in report.results] != urls:
        raise RuntimeError("pipeline did not return exactly one ordered result per URL")

    rows: list[HarnessRow] = []
    for result in report.results:
        seconds = elapsed.get(result.url)
        if seconds is None:
            raise RuntimeError(f"pipeline did not record elapsed time for {result.url}")
        video_available = pipeline.video_available.get(result.url)
        rows.append(
            HarnessRow(
                url=result.url,
                outcome=result_outcome(result),
                elapsed_seconds=round(seconds, 3),
                failure_class=failure_class(
                    result, video_available=video_available
                ),
                fetch_status=result.fetch_status.value,
                video_available=video_available,
            )
        )
    return rows


def summarize(rows: list[HarnessRow]) -> dict[str, object]:
    """Build stable counts for people and the future dashboard."""
    total = len(rows)
    successful = sum(row.outcome == "success" for row in rows)
    failures = {name: 0 for name in FAILURE_CLASSES}
    for row in rows:
        if row.failure_class is not None:
            failures[row.failure_class] += 1
    return {
        "total_tested": total,
        "successful": successful,
        "failed": total - successful,
        "pass_rate": successful / total if total else 0.0,
        "failure_breakdown": failures,
    }


def render_summary(summary: dict[str, object]) -> str:
    """Return a compact terminal table."""
    total = int(summary["total_tested"])
    failures = summary["failure_breakdown"]
    assert isinstance(failures, dict)
    lines = [
        "CAPTURE HARNESS SUMMARY",
        "-----------------------",
        f"Total tested : {total}",
        f"Successful   : {summary['successful']}",
        f"Pass rate    : {float(summary['pass_rate']):.1%}",
        "",
        "Failure class   Count   Percent",
        "--------------- -----   -------",
    ]
    for name in FAILURE_CLASSES:
        count = int(failures[name])
        percent = count / total if total else 0.0
        lines.append(f"{name:<15} {count:>5}   {percent:>7.1%}")
    return "\n".join(lines)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path.resolve())


def build_artifact(
    *,
    source: Path,
    seed: int,
    eligible_count: int,
    selected_urls: list[str],
    rows: list[HarnessRow],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement": {
            "scope": "public logged-out fetch + embed fallback + heuristic extraction + validation",
            "ollama": False,
            "transcription": False,
            "ocr": False,
            "geocoding": False,
            "temporal_resolution": False,
            "authenticated_source": False,
            "jsonl_sink": False,
            "chroma_sink": False,
            "raw_snapshots": False,
        },
        "selection": {
            "source": _display_path(source),
            "source_sha256": hashlib.sha256(
                source.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest(),
            "method": "random.Random(seed).sample(sorted(unique_reel_urls), sample_size)",
            "seed": seed,
            "eligible_urls": eligible_count,
            "sample_size": len(selected_urls),
            "selected_urls": selected_urls,
        },
        "summary": summarize(rows),
        "results": [row.to_dict() for row in rows],
    }


def validate_report_path(path: Path) -> Path:
    """Keep the sole generated artifact inside the agreed tracked directory."""
    resolved = path.resolve()
    report_root = REPORT_DIR.resolve()
    if report_root not in resolved.parents or resolved.suffix.lower() != ".json":
        raise ValueError("--output must be a .json file inside docs/results/")
    return resolved


def write_artifact(path: Path, artifact: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("fixtures/labels.jsonl"),
        help="UTF-8 JSONL corpus used only for its URL fields",
    )
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--headed", action="store_true", help="show Chromium while measuring")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args(argv)
    try:
        output = validate_report_path(args.output)
        eligible = load_eligible_urls(args.source)
        selected = select_urls(eligible, args.sample_size, args.seed)
    except (OSError, ValueError) as exc:
        print(f"capture harness: {exc}", file=sys.stderr)
        return 2

    print(
        f"Selected {len(selected)} of {len(eligible)} eligible reels "
        f"from {_display_path(args.source)} with seed {args.seed}."
    )
    print("Optional services and all persistence are disabled; starting live public fetches.")
    rows = asyncio.run(run_capture(selected, settings=harness_settings(headed=args.headed)))
    artifact = build_artifact(
        source=args.source,
        seed=args.seed,
        eligible_count=len(eligible),
        selected_urls=selected,
        rows=rows,
    )
    write_artifact(output, artifact)
    print("\n" + render_summary(artifact["summary"]))
    print(f"\nJSON report: {_display_path(output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
