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


def get_service() -> RecommendationService:
    """Lazily build the service (and its ChromaSink) on first use."""
    global _service
    if _service is None:
        from src.ingestion.sinks.chroma_sink import ChromaSink

        _service = RecommendationService(ChromaSink(_settings), _settings)
    return _service


class RecommendRequest(BaseModel):
    channel_id: str
    message: str
    mode: str = "command"          # "command" (explicit /events) | "auto" (chat-context)
    category: Optional[str] = None


@app.post("/recommend")
def recommend(req: RecommendRequest):
    result = get_service().recommend(
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


@app.post("/plan")
def plan(req: PlanRequest):
    """Group planning: chat transcript -> synthesized request + a shortlist."""
    result = get_service().plan(req.channel_id, req.transcript)
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
