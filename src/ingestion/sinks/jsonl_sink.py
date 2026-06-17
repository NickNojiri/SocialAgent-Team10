"""Append-only JSONL sink — the Phase 1 handoff format.

A future chroma_sink can follow src/logic/database.py's PreferenceStore with a
separate 'event_inspirations' collection; JSONL stays the source of truth.
"""

from pathlib import Path

from src.ingestion.schemas.results import IngestionResult


class JsonlSink:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, result: IngestionResult) -> None:
        if result.record is None:
            return  # failures are reported via IngestionResult, not persisted here
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(result.record.model_dump_json() + "\n")
