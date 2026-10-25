"""Short, user-facing descriptions of a reel, written by the local LLM (Ollama).

  * summarize_place — the 1-2 sentence "quick description" on a spot card.
  * summarize_post  — what a reel that ISN'T a place is about (a skit, a story, a product),
                      so the bot can still say something useful instead of only "not a place".
  * describe_frames — what the video shows, from a few still frames, via a local vision
                      model (gemma3 by default); feeds both summaries.

All best-effort: each returns None on any failure or when there's nothing to describe, and
the caller carries on. Everything runs on this machine (ADR-0002): no post text leaves it.
Output length is capped (num_predict) to keep captures fast.
"""

import base64
import logging
from typing import Optional

import httpx

from src.ingestion.config import IngestionSettings

log = logging.getLogger("ingestion.summarizer")

_STYLE = "Plain text, no hype, no emojis, no preamble."


def summarize_place(
    caption: Optional[str], transcript: Optional[str], settings: IngestionSettings,
    visual: Optional[str] = None,
) -> Optional[str]:
    caption, transcript, visual = (caption or "").strip(), (transcript or "").strip(), (visual or "").strip()
    if not transcript and not caption and not visual:
        return None
    prompt = (
        "Write a 1-2 sentence description of this place for someone scrolling, like a quick "
        f"video summary. Say what they serve or the vibe, and the location if it's known. {_STYLE}\n\n"
        + _sources(caption, transcript, visual)
    )
    return _generate(settings.ollama_model, prompt, settings, label="summary")


def summarize_post(
    caption: Optional[str], transcript: Optional[str], visual: Optional[str], settings: IngestionSettings,
) -> Optional[str]:
    """2-3 sentences on what the whole video is about, for a reel that didn't make a spot."""
    caption, transcript, visual = (caption or "").strip(), (transcript or "").strip(), (visual or "").strip()
    if not transcript and not visual and len(caption) < 20:
        return None                      # a one-line caption alone isn't worth summarizing
    prompt = (
        "Summarize this short social-media video in 2-3 sentences for a friend in a group chat: "
        "what happens and what it's about. Use what is said and what is shown, not only the "
        f"caption. Don't invent details. {_STYLE}\n\n" + _sources(caption, transcript, visual)
    )
    return _generate(settings.ollama_model, prompt, settings, label="post summary", num_predict=160)


def describe_frames(frames: list[bytes], settings: IngestionSettings) -> Optional[str]:
    """What a few still frames (a few seconds apart; usually one contact sheet of them) show,
    in 1-2 sentences."""
    if not frames:
        return None
    layout = ("a grid of still frames, read left to right then top to bottom," if len(frames) == 1
              else "still frames, in order,")
    prompt = (
        f"This is {layout} from one short social-media video. In 1-2 sentences, describe what "
        "is shown: the setting, the people or things, and any readable text or signs. Only "
        f"describe what is clearly visible. {_STYLE}"
    )
    images = [base64.b64encode(f).decode("ascii") for f in frames]
    return _generate(settings.vision_model, prompt, settings, label="frames", images=images, num_predict=120)


def _sources(caption: str, transcript: str, visual: str) -> str:
    return (f"Caption:\n{caption or '(none)'}\n\nSpoken audio:\n{transcript or '(none)'}\n\n"
            f"What the video shows:\n{visual or '(unknown)'}\n")


def _generate(model: str, prompt: str, settings: IngestionSettings, *, label: str,
              images: Optional[list[str]] = None, num_predict: int = 120) -> Optional[str]:
    # Same num_ctx as the extractor: Ollama reloads a model whenever num_ctx changes, and its
    # own default (32k on 0.23) also made llama3.2 take 8.5 GB, evicting the vision model.
    # Matching it took a capture's model calls from 38 s to 16 s on an 8 GB GPU.
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"num_predict": num_predict, "num_ctx": settings.llm_num_ctx}}
    if images:
        body["images"] = images
    try:
        resp = httpx.post(f"{settings.ollama_url}/api/generate", json=body, timeout=settings.llm_timeout_s)
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip()
        return text or None
    except Exception as exc:
        log.warning(f"[{label}] failed: {type(exc).__name__}: {exc}")
        return None
