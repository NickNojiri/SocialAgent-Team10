"""Short, user-facing descriptions of a reel, written by the local LLM (Ollama).

  * summarize_place — the 1-2 sentence "quick description" on a spot card.
  * describe_post   — a title, a kind and 2-3 sentences for a reel that ISN'T a place (a skit,
                      a recipe, a meme), so the bot can still say what it's about.
  * describe_frames — what the video shows, from a few still frames, via a local vision
                      model (gemma3 by default); feeds both summaries.

All best-effort: each returns None on any failure or when there's nothing to describe, and
the caller carries on. Everything runs on this machine (ADR-0002): no post text leaves it.
Output length is capped (num_predict) to keep captures fast.
"""

import base64
import json
import logging
import re
import unicodedata
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


# What a reel that isn't a place can be. The same reasons Nick gave in the burner review
# (experiments/burner_study/post_type.py), so a card's kind is also a training label; "story"
# is the catch-all. Adding a kind is one entry here and one in app/cards.py::GIST_KINDS.
POST_KINDS = ("recipe", "meme", "game", "music", "ad", "story")

_TITLE_LEADS = ("this video", "this reel", "the video", "the reel", "a video", "a reel", "video of",
                "untitled", "title:")
_URLISH = re.compile(r"https?://|www\.|\.com\b", re.IGNORECASE)


def describe_post(
    caption: Optional[str], transcript: Optional[str], visual: Optional[str], author: Optional[str],
    settings: IngestionSettings,
) -> Optional[dict]:
    """{title, kind, summary} for a reel that read fine but didn't make a spot: a short title from
    what's said (or, with nothing said, what's shown), one of POST_KINDS, and 2-3 sentences, so
    someone can tell what it's about without watching it. None when there's nothing to go on."""
    caption, transcript, visual = (caption or "").strip(), (transcript or "").strip(), (visual or "").strip()
    if not transcript and not visual and len(caption) < 20:
        return None                      # a one-line caption alone isn't worth summarizing
    prompt = (
        "This short social-media video is not about a place to go. Return JSON with three keys:\n"
        '  "title": a natural 3-8 word title, like a headline a friend would write. Base it on '
        "what is said; if nothing is said, on what is shown. No hashtags, @names, emojis or quotes.\n"
        f'  "kind": one of {", ".join(POST_KINDS)} ("story" if none fits).\n'
        '  "summary": 2-3 sentences on what happens and what it is about, for a friend in a group '
        f"chat. Use what is said and shown, not only the caption. Don't invent details. {_STYLE}\n\n"
        + _sources(caption, transcript, visual)
    )
    raw = _generate(settings.ollama_model, prompt, settings, label="post summary", num_predict=220, json_mode=True)
    try:
        out = json.loads(raw or "")
    except ValueError:
        out = None
    if not isinstance(out, dict):
        return {"title": fallback_title(author), "kind": "story", "summary": raw.strip()} if raw else None
    summary = " ".join(str(out.get("summary") or "").split())
    if not summary:
        return None
    kind = str(out.get("kind") or "").strip().lower()
    return {
        "title": clean_title(out.get("title"), caption, author),
        "kind": kind if kind in POST_KINDS else "story",
        "summary": summary,
    }


def fallback_title(author: Optional[str]) -> str:
    handle = (author or "").strip().lstrip("@")
    return f"@{handle}'s reel" if handle else "A reel"


def clean_title(title, caption: Optional[str], author: Optional[str]) -> str:
    """The model's title, or "@account's reel" when it's too weird to show: under 2 or over 10
    words; a hashtag, @, link or emoji; mostly capitals; a description ("This video…"); or just
    the caption's first line again."""
    text = " ".join(str(title or "").split()).strip("\"'“”‘’ ").rstrip(".")
    words = text.split()
    letters = [c for c in text if c.isalpha()]
    first_line = " ".join(((caption or "").strip().split("\n") or [""])[0].split()).casefold()
    weird = (
        not 2 <= len(words) <= 10
        or "#" in text or "@" in text or _URLISH.search(text)
        or any(unicodedata.category(c) == "So" or ord(c) >= 0x1F000 for c in text)
        or (len(letters) >= 4 and sum(c.isupper() for c in letters) > 0.7 * len(letters))
        or text.casefold().startswith(_TITLE_LEADS)
        or (first_line and text.casefold() == first_line)
    )
    return fallback_title(author) if weird else text


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
              images: Optional[list[str]] = None, num_predict: int = 120,
              json_mode: bool = False) -> Optional[str]:
    # Same num_ctx as the extractor: Ollama reloads a model whenever num_ctx changes, and its
    # own default (32k on 0.23) also made llama3.2 take 8.5 GB, evicting the vision model.
    # Matching it took a capture's model calls from 38 s to 16 s on an 8 GB GPU.
    body = {"model": model, "prompt": prompt, "stream": False,
            "options": {"num_predict": num_predict, "num_ctx": settings.llm_num_ctx}}
    if images:
        body["images"] = images
    if json_mode:
        body["format"] = "json"
    try:
        resp = httpx.post(f"{settings.ollama_url}/api/generate", json=body, timeout=settings.llm_timeout_s)
        resp.raise_for_status()
        text = (resp.json().get("response") or "").strip()
        return text or None
    except Exception as exc:
        log.warning(f"[{label}] failed: {type(exc).__name__}: {exc}")
        return None
