"""Entry point: explicit public URLs in, validated EventInspiration records out.

    python -m src.ingestion.cli <url> [<url> ...] [--out data/inspirations.jsonl]
    python -m src.ingestion.cli --from-file urls.txt
    python -m src.ingestion.cli <url> --no-llm        # heuristics only

No crawling or discovery — only the URLs given are fetched, one at a time,
with per-domain throttling. Blocked pages are reported honestly, not retried.

By default a local Ollama model refines the extracted fields; if Ollama is
unreachable (or --no-llm is set) the pipeline degrades to Phase 1 heuristics.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx

from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.geo_enricher import GeoEnricher
from src.ingestion.pipeline.llm_extractor import LlmFieldExtractor
from src.ingestion.pipeline.orchestrator import IngestionPipeline, result_line
from src.ingestion.pipeline.temporal_resolver import TemporalResolver
from src.ingestion.sinks.jsonl_sink import JsonlSink

if TYPE_CHECKING:  # heavy import (chromadb) only loaded when --chroma is used
    from src.ingestion.sinks.chroma_sink import ChromaSink


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="socialagent-ingest",
        description="Parse public social/event pages into Event Inspiration records.",
    )
    parser.add_argument("urls", nargs="*", help="public post/page URLs")
    parser.add_argument("--from-file", type=Path, help="file with one URL per line")
    parser.add_argument("--out", type=Path, default=Path("data/inspirations.jsonl"))
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    parser.add_argument("--no-llm", action="store_true", help="skip the LLM layer; heuristics only")
    parser.add_argument("--no-geocode", action="store_true", help="skip geo enrichment (no Nominatim calls)")
    parser.add_argument("--no-temporal", action="store_true", help="skip temporal parsing (no schedule)")
    parser.add_argument("--chroma", action="store_true", help="also write records to the ChromaDB vector store")
    parser.add_argument("--report-json", type=Path, help="write the run report as machine-readable JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress per-URL progress lines")
    parser.add_argument("--model", help="override the Ollama model (default: llama3.1:8b)")
    # Offline fixtures/tests only; hidden from --help on purpose.
    parser.add_argument("--allow-file-urls", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.from_file:
        lines = args.from_file.read_text(encoding="utf-8").splitlines()
        args.urls += [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    if not args.urls:
        parser.error("no URLs given (positional or --from-file)")
    return args


def build_extractor(settings: IngestionSettings) -> Optional[LlmFieldExtractor]:
    """Return a ready extractor, or None (with a printed reason) to use heuristics."""
    if not settings.llm_enabled:
        print("[llm] disabled - using heuristics only")
        return None

    reachable, detail = _check_ollama(settings)
    if not reachable:
        print(f"[llm] Ollama unavailable ({detail}) - falling back to heuristics only")
        return None

    print(f"[llm] using {settings.ollama_model} @ {settings.ollama_url}")
    return LlmFieldExtractor(settings)


def build_geo_enricher(settings: IngestionSettings) -> Optional[GeoEnricher]:
    """Return a GeoEnricher, or None (with a printed reason) to skip enrichment."""
    if not settings.geocode_enabled:
        print("[geo] disabled - coordinates will not be resolved")
        return None
    try:
        enricher = GeoEnricher(settings)  # constructs the default geocoder (imports requests)
    except ImportError as exc:
        print(f"[geo] geocoder unavailable ({exc}) - skipping enrichment")
        return None
    print("[geo] enrichment on - resolving location clues via Nominatim")
    return enricher


def build_temporal_resolver(settings: IngestionSettings) -> Optional[TemporalResolver]:
    """Return a TemporalResolver, or None (with a printed reason) to skip parsing."""
    if not settings.temporal_enabled:
        print("[time] disabled - candidate_times left unresolved")
        return None
    print("[time] temporal parsing on - resolving candidate_times to UTC schedules")
    return TemporalResolver(settings)


def build_chroma_sink(settings: IngestionSettings) -> Optional["ChromaSink"]:
    """Return a ChromaSink, or None (with a printed reason) to skip vector storage."""
    if not settings.chroma_enabled:
        return None
    # Embeddings come from Ollama; if the embed model isn't reachable, skip Chroma
    # rather than failing the run (JSONL stays the source of truth).
    reachable, detail = _check_ollama(IngestionSettings(ollama_model=settings.embed_model))
    if not reachable:
        print(f"[chroma] embed model unavailable ({detail}) - skipping vector store")
        return None
    from src.ingestion.sinks.chroma_sink import ChromaSink

    print(f"[chroma] vector store on - collection '{settings.chroma_collection}' @ {settings.chroma_path}")
    return ChromaSink(settings)


def _check_ollama(settings: IngestionSettings) -> tuple[bool, str]:
    """GET /api/tags and confirm the configured model is present (pattern from llm/app.py)."""
    try:
        resp = httpx.get(f"{settings.ollama_url}/api/tags", timeout=5.0)
        resp.raise_for_status()
        available = [m["name"] for m in resp.json().get("models", [])]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if settings.ollama_model not in available:
        return False, f"model '{settings.ollama_model}' not pulled (run: ollama pull {settings.ollama_model})"
    return True, f"{len(available)} model(s) available"


def main(argv=None) -> int:
    # The diagnostic report and venue names can contain non-ASCII (emoji, accents).
    # On Windows a redirected stdout defaults to cp1252 and would crash on those;
    # force UTF-8 so the run never dies at the print stage.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    args = parse_args(argv)
    settings = IngestionSettings(
        headless=not args.headed,
        allow_file_urls=args.allow_file_urls,
        raw_dir=args.raw_dir,
        llm_enabled=not args.no_llm,
        geocode_enabled=not args.no_geocode,
        temporal_enabled=not args.no_temporal,
        chroma_enabled=args.chroma,
        **({"ollama_model": args.model} if args.model else {}),
    )
    # Build the (optional) stage components — each prints its status / degrades.
    pipeline = IngestionPipeline(
        settings,
        extractor=build_extractor(settings),
        geo_enricher=build_geo_enricher(settings),
        temporal_resolver=build_temporal_resolver(settings),
        jsonl_sink=JsonlSink(args.out),
        chroma_sink=build_chroma_sink(settings),
        on_result=None if args.quiet else lambda r: print(result_line(r)),  # live per-URL progress
    )
    report = asyncio.run(pipeline.run(args.urls))
    if args.report_json:
        args.report_json.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print("\n" + report.render())
    return 0 if report.validated else 1


if __name__ == "__main__":
    sys.exit(main())
