"""Recommendation HTTP service (Phase 6).

A lightweight FastAPI app the Discord bot calls over HTTP — mirroring how the bot
already calls the LLM/DB services. Deps: fastapi + chromadb only (no langchain).
Run: uvicorn src.ingestion.serving.app:app --port 8003
"""

import logging
import os
from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from src.ingestion.config import IngestionSettings
from src.ingestion.serving.discord_format import format_recommendations
from src.ingestion.serving.recommender import RecommendationService

log = logging.getLogger("ingestion.serving")

app = FastAPI(title="SocialAgent Recommendations")
# IngestionSettings isn't env-bound, so read the container-relevant knobs here:
# in Docker, Ollama is reached via host.docker.internal and data/ is a bind mount.
_settings = IngestionSettings(
    ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
    chroma_path=os.getenv("CHROMA_PATH", "data"),
)
_service: Optional[RecommendationService] = None
_services: dict[str, RecommendationService] = {}


def _build_service(guild_id: str = "") -> RecommendationService:
    from src.ingestion.sinks.chroma_sink import ChromaSink, collection_for_guild

    sink = ChromaSink(_settings, collection_name=collection_for_guild(_settings, guild_id))
    return RecommendationService(sink, _settings)


def get_service(guild_id: str = "") -> RecommendationService:
    """Lazily build one service (and ChromaSink) per guild catalog.

    "" is the legacy/single-tenant catalog and keeps the `_service` global so
    existing self-hosts (and tests that patch it) behave exactly as before.
    """
    global _service
    key = str(guild_id or "")
    if key == "":
        if _service is None:
            _service = _build_service("")
        return _service
    if key not in _services:
        _services[key] = _build_service(key)
    return _services[key]


class RecommendRequest(BaseModel):
    channel_id: str
    message: str
    mode: str = "command"          # "command" (explicit /events) | "auto" (chat-context)
    category: Optional[str] = None
    guild_id: str = ""             # "" → the legacy/single-tenant catalog


@app.post("/recommend")
def recommend(req: RecommendRequest):
    result = get_service(req.guild_id).recommend(
        req.channel_id, req.message, mode=req.mode, category=req.category
    )
    return {
        "suppressed": result.suppressed,
        "reason": result.reason,
        "recommendations": [asdict(r) for r in result.recommendations],
        "markdown": format_recommendations(result.recommendations),
    }


class PlanRequest(BaseModel):
    channel_id: str
    transcript: str          # the recent multi-person chat the bot collected
    guild_id: str = ""       # "" → the legacy/single-tenant catalog


@app.post("/plan")
def plan(req: PlanRequest):
    """Group planning: chat transcript -> synthesized request + a shortlist."""
    result = get_service(req.guild_id).plan(req.channel_id, req.transcript)
    return {
        "request": result.request,
        "query": result.query,
        "recommendations": [asdict(r) for r in result.recommendations],
        "markdown": format_recommendations(result.recommendations),
    }


from fastapi.responses import JSONResponse

@app.get("/ready")
def ready():
    try:
        count = get_service().sink.count()
        return {"ready": True, "events": count}
    except Exception as e:
        return JSONResponse(status_code=503, content={"ready": False, "reason": str(e)})

@app.get("/health")
def health():
    return {"status": "ok", "collection": _settings.chroma_collection}
