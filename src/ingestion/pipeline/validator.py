"""Promotes raw candidates to a strict EventInspiration — or explains why not.

Orchestrates the optional LLM layer: build payload → extract → normalize → validate.
The LLM gets no schema bypass; whatever it produces still has to pass strict
EventInspiration validation here. Every rejection carries a reason.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from pydantic import ValidationError

from src.ingestion.pipeline.normalizer import build_llm_payload, normalize
from src.ingestion.schemas.inspiration import (
    EventInspiration,
    SourceProvenance,
    content_hash,
)
from src.ingestion.schemas.snapshot import RawPostSnapshot

if TYPE_CHECKING:  # avoid importing httpx at module load for the heuristics-only path
    from src.ingestion.pipeline.llm_extractor import LlmFieldExtractor


def build_record(
    raw: RawPostSnapshot,
    extractor: Optional["LlmFieldExtractor"] = None,
) -> tuple[Optional[EventInspiration], Optional[str]]:
    """Returns (record, None) on success or (None, rejection_reason)."""
    basis = raw.caption or raw.description or raw.title or ""
    if not basis.strip():
        return None, "no human-readable text was extracted from the page"

    # LLM layer is best-effort: a None result (Ollama down, unparseable, or
    # ungrounded) means we proceed with heuristics only.
    llm_extraction = None
    if extractor is not None:
        llm_extraction = extractor.extract(build_llm_payload(raw))

    candidates = normalize(raw, llm_extraction=llm_extraction)

    # Not an EventInspiration field: a heuristic "this isn't a place/event at all"
    # gate (product ads, music clips) so the catalog stays clean without an LLM.
    if candidates.pop("is_vague", False):
        return None, "post is not clearly about a place, event, or food"

    # Extraction-quality signals ride in provenance, not the record body.
    venue_slot = candidates.pop("venue_slot", None)
    venue_confidence = candidates.pop("venue_confidence", 0.5)

    if extractor is None:
        extractor_tag = raw.extractor                       # Phase 1 behavior, unchanged
    elif llm_extraction is not None:
        extractor_tag = f"{raw.extractor}+llm:{extractor.model}"
    else:
        extractor_tag = f"{raw.extractor}+heuristic"        # LLM attempted, fell back

    platform = raw.platform if raw.platform in ("instagram", "tiktok", "generic") else "generic"
    provenance = {
        "source_url": raw.source_url,
        "platform": platform,
        "fetched_at": raw.fetched_at or datetime.now(timezone.utc),
        "content_hash": content_hash(basis),
        "extractor": extractor_tag,
        "venue_slot": venue_slot,
        "venue_confidence": venue_confidence,
    }

    try:
        record = EventInspiration(
            **candidates, provenance=SourceProvenance(**provenance)
        )
    except ValidationError as exc:
        issues = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        return None, issues

    return record, None
