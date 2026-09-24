"""Recommendation HTTP service (Phase 6).

A lightweight FastAPI app the Discord bot calls over HTTP — mirroring how the bot
already calls the LLM/DB services. Deps: fastapi + chromadb only (no langchain).
Run: uvicorn src.ingestion.serving.app:app --port 8003
"""

import logging
import os
from dataclasses import asdict
from typing import Optional

from fastapi import FastAPI, Header
from pydantic import BaseModel

from src.ingestion.config import IngestionSettings
from src.ingestion.serving.discord_format import format_recommendations
from src.ingestion.serving.recommender import RecommendationService
from src.ingestion.serving.tenant_auth import SCOPE_READ, authorize

log = logging.getLogger("ingestion.serving")

app = FastAPI(title="SocialAgent Recommendations")
# IngestionSettings isn't env-bound, so read the container-relevant knobs here:
# in Docker, Ollama is reached via host.docker.internal and data/ is a bind mount.
# embed_model defaults to the config value the admin app writes the catalog with;
# a different model means every query fails on a vector-dimension mismatch.
_settings = IngestionSettings(
    ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
    chroma_path=os.getenv("CHROMA_PATH", "data"),
    embed_model=os.getenv("EMBED_MODEL") or IngestionSettings.model_fields["embed_model"].default,
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
    if key not in _services or not _still_there(_services[key]):
        _services[key] = _build_service(key)
    return _services[key]


def _still_there(service: RecommendationService) -> bool:
    """False once the admin app deleted this server's catalog (#21): Chroma then
    fails every call on the old handle, so it's rebuilt instead."""
    try:
        service.sink.collection.count()
        return True
    except Exception:
        return False


class RecommendRequest(BaseModel):
    channel_id: str
    message: str
    mode: str = "command"          # "command" (explicit /events) | "auto" (chat-context)
    category: Optional[str] = None
    guild_id: str = ""             # "" → the legacy/single-tenant catalog


# Both endpoints return spots from the catalog named by guild_id, so they check
# the same signed tenant token as the admin app (docs/THREAT_MODEL.md T2).
# docker-compose publishes this port, which is why it can't rely on localhost.
TenantToken = Header(default=None, alias="X-Tenant-Token")


@app.post("/recommend")
def recommend(req: RecommendRequest, x_tenant_token: Optional[str] = TenantToken):
    authorize(req.guild_id, x_tenant_token, need=SCOPE_READ)
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
    user_id: str = ""        # ML Layer 1: personalise by the requester's vote history


@app.post("/plan")
def plan(req: PlanRequest, x_tenant_token: Optional[str] = TenantToken):
    """Group planning: chat transcript -> synthesized request + a shortlist."""
    authorize(req.guild_id, x_tenant_token, need=SCOPE_READ)
    result = get_service(req.guild_id).plan(
        req.channel_id, req.transcript, user_id=req.user_id
    )
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
