"""Short, user-facing "quick description" of a place from its caption + reel audio.

A best-effort local-LLM (Ollama) call — like a quick video summary. Returns None
on any failure or when there's nothing to describe, so the caller shows "No info".
Capped output (num_predict) keeps it fast; the model is the same one the extractor
uses, so descriptions stay consistent.
"""

import logging
from typing import Optional

import httpx

from src.ingestion.config import IngestionSettings

log = logging.getLogger("ingestion.summarizer")


def summarize_place(
    caption: Optional[str], transcript: Optional[str], settings: IngestionSettings
) -> Optional[str]:
    caption = (caption or "").strip()
    transcript = (transcript or "").strip()
    if not transcript and not caption:
        return None
    prompt = (
        "Write a 1-2 sentence description of this place for someone scrolling, like a quick "
        "video summary. Say what they serve or the vibe, and the location if it's known. "
        "Plain text, no hype, no emojis, no preamble.\n\n"
        f"Caption:\n{caption or '(none)'}\n\nSpoken audio:\n{transcript or '(none)'}\n"
    )
    try:
        resp = httpx.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 120},   # cap length → keep it fast
            },
            timeout=settings.llm_timeout_s,
        )
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip()
        return text or None
    except Exception as exc:
        log.warning(f"[summary] failed: {type(exc).__name__}: {exc}")
        return None
