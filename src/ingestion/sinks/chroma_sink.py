"""ChromaDB vector sink (Phase 5).

Mirrors the structural pattern of src/logic/database.py::PreferenceStore
(chromadb PersistentClient -> get_or_create_collection -> upsert with
ids/documents/metadatas) in a dedicated, isolated `event_inspirations`
collection, so downstream agents (Phase 6) can run similarity search over
event vibes/themes/categories.

Two deliberate differences from PreferenceStore:
  - embeddings are computed explicitly by one injectable embedder and passed as
    `embeddings=` to upsert/query, so write and read use the *same* vectors and
    Chroma's default onnx embedder (a blocked HuggingFace download) is never hit;
  - no langchain wrapper — just the chromadb primitives PreferenceStore uses.

Dedup: the Chroma id is provenance.content_hash, so re-ingesting the same post
upserts one vector instead of duplicating. JSONL remains the source of truth;
this sink mirrors JsonlSink.write so the two are interchangeable in the pipeline.
"""

import logging
from typing import Callable, Optional

import httpx

from src.ingestion.config import IngestionSettings
from src.ingestion.schemas.inspiration import EventInspiration, ScheduleStatus
from src.ingestion.schemas.results import IngestionResult

log = logging.getLogger("ingestion.chroma")

Embedder = Callable[[list[str]], list[list[float]]]


class OllamaEmbedder:
    """Local embeddings via Ollama (mirrors PreferenceStore's OllamaEmbeddings).
    localhost only — works on networks that intercept outbound TLS."""

    def __init__(self, settings: IngestionSettings):
        self.url = f"{settings.ollama_url}/api/embeddings"
        self.model = settings.embed_model
        self.timeout = settings.llm_timeout_s

    def __call__(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            resp = httpx.post(
                self.url,
                json={"model": self.model, "prompt": text},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            vectors.append(resp.json()["embedding"])
        return vectors


class ChromaSink:
    def __init__(self, settings: IngestionSettings, embedder: Optional[Embedder] = None):
        import chromadb  # lazy: only when Chroma is actually used

        self.client = chromadb.PersistentClient(path=settings.chroma_path)
        self.collection = self.client.get_or_create_collection(
            name=settings.chroma_collection,
            metadata={"hnsw:space": "cosine"},
        )
        self.embedder = embedder or OllamaEmbedder(settings)

    # ── write path (mirrors JsonlSink.write) ─────────────────────────────────

    def write(self, result: IngestionResult) -> None:
        if result.record is None:
            return  # failures are reported via IngestionResult, not persisted
        try:
            self.add(result.record)
        except Exception as exc:  # embedder/Ollama/Chroma error must not fail the run
            log.warning(f"[chroma] skipped {result.url}: {type(exc).__name__}: {exc}")

    def add(self, record: EventInspiration) -> str:
        doc = _vibe_document(record)
        embedding = self.embedder([doc])[0]
        record_id = record.provenance.content_hash  # stable dedup key
        self.collection.upsert(
            ids=[record_id],
            documents=[doc],
            metadatas=[_metadata(record)],
            embeddings=[embedding],
        )
        return record_id

    # ── read path (Phase 6 entry point) ──────────────────────────────────────

    def query(self, text: str, k: int = 5, where: Optional[dict] = None) -> list[dict]:
        embedding = self.embedder([text])[0]
        res = self.collection.query(
            query_embeddings=[embedding],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        out: list[dict] = []
        for doc, meta, dist in zip(
            res["documents"][0], res["metadatas"][0], res["distances"][0]
        ):
            out.append({"document": doc, "metadata": meta, "distance": dist})
        return out

    def count(self) -> int:
        return self.collection.count()

    def get(self, content_hash: str) -> Optional[dict]:
        res = self.collection.get(ids=[content_hash], include=["documents", "metadatas"])
        if not res["ids"]:
            return None
        return {"id": res["ids"][0], "document": res["documents"][0], "metadata": res["metadatas"][0]}


# ── document + metadata mapping ──────────────────────────────────────────────


def _vibe_document(record: EventInspiration) -> str:
    """The text similarity runs over — themes, venue, category, vibe."""
    vibe = " ".join(record.hashtags)
    return (
        f"{record.core_theme}. Venue: {record.venue_name}. "
        f"Category: {record.category.value}. Vibe: {vibe}".strip()
    )


def _metadata(record: EventInspiration) -> dict:
    """Chroma metadata accepts only str|int|float|bool — drop None, join lists."""
    geo = record.geo
    prov = record.provenance
    meta: dict = {
        "venue_name": record.venue_name,
        "core_theme": record.core_theme,
        "category": record.category.value,
        "platform": prov.platform,
        "tags": ",".join(record.hashtags),
        "source_url": str(prov.source_url),
        "content_hash": prov.content_hash,
        "record_id": str(record.record_id),
        "fetched_at": prov.fetched_at.isoformat(),
        "geo_resolution": geo.resolution.value,
        "geo_confidence": geo.confidence,
    }
    if getattr(record, "summary", None):
        meta["summary"] = record.summary
    if getattr(record, "image_url", None):
        meta["image_url"] = record.image_url
    if geo.lat is not None and geo.lng is not None:
        meta["lat"] = geo.lat
        meta["lng"] = geo.lng
    if geo.raw_location_text:
        meta["raw_location_text"] = geo.raw_location_text

    sched = record.schedule
    if sched is not None:
        meta["schedule_status"] = sched.status.value
        meta["time_known"] = sched.time_known
        if sched.status is ScheduleStatus.SCHEDULED and sched.start_utc is not None:
            meta["start_utc"] = sched.start_utc.isoformat()
            meta["start_epoch"] = int(sched.start_utc.timestamp())
            if sched.end_utc is not None:
                meta["end_utc"] = sched.end_utc.isoformat()
                meta["end_epoch"] = int(sched.end_utc.timestamp())

    return meta
