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
        out = _salvage(raw or "")
        if out is None:
            return None
    summary = out.get("summary")
    summary = " ".join(summary.split()) if isinstance(summary, str) else ""   # never a dict's repr on a card
    if not summary:
        return None
    kind = str(out.get("kind") or "").strip().lower()
    return {
        "title": clean_title(out.get("title"), caption, author),
        "kind": kind if kind in POST_KINDS else "story",
        "summary": summary,
    }


_JSON_FIELD = re.compile(r'"(title|kind|summary)"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL)


def _salvage(raw: str) -> Optional[dict]:
    """What can be read from a reply that isn't a JSON object: usually one cut off at the
    token limit mid-summary. Plain prose is the summary itself; broken JSON gives up its
    fields one by one, and never shows on a card as raw braces and quotes."""
    text = raw.strip()
    if not text:
        return None
    if not text.startswith(("{", "[", '"')):
        return {"summary": text}
    out = {}
    for key, value in _JSON_FIELD.findall(text):
        try:
            out[key] = json.loads(f'"{value}"')
        except ValueError:
            out[key] = value.rstrip("\\")
    return out or None


_STRIP = re.compile(r"https?://\S+|www\.\S+|[#@][\w.]+")


def _plain_text(text) -> str:
    """Text on one line, without links, #tags, @handles or emoji."""
    if not isinstance(text, str):
        return ""
    text = _STRIP.sub(" ", text)
    text = "".join(c for c in text
                   if unicodedata.category(c) not in ("So", "Cf", "Cc", "Cs") and ord(c) < 0x1F000)
    return " ".join(text.split())


def plain_title(caption: Optional[str], transcript: Optional[str] = None) -> str:
    """A title that doesn't try hard: the first few words of the caption (or of what's said),
    else "A reel". For when the model's title is unusable or the model is down. Never the
    poster's @handle: Nick, 2026-10-27, the card shouldn't read as being about the account."""
    for source in (caption, transcript):
        if not isinstance(source, str) or not source.strip():
            continue
        first = re.split(r"(?<=[.!?])\s|\n", source.strip())[0]
        words = _plain_text(first).strip(" -–—:;,.!?\"'").split()
        if len(words) >= 2:
            title = " ".join(words[:6])[:60].rstrip(" ,.;:-")
            return title[0].upper() + title[1:]
    return "A reel"


def placeholder_gist(caption: Optional[str], transcript: Optional[str] = None) -> dict:
    """A not-a-place write-up made without the model (it failed or is busy), so a reel that was
    read still gets a card instead of "I read it, but couldn't tell": a plain title and the
    caption (or speech) itself, cut to a few lines."""
    text = _plain_text(caption) or _plain_text(transcript)
    if len(text) > 240:
        text = text[:240].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return {"title": plain_title(caption, transcript), "kind": "story", "summary": text}


def clean_title(title, caption: Optional[str], author: Optional[str] = None) -> str:
    """The model's title, or a plain one from the caption (plain_title) when it's too weird to
    show: not text; under 2 or over 10 words, or over 100 characters; a hashtag, @, link or
    emoji; mostly capitals; a description ("This video…"); or just the caption's first line
    again."""
    text = " ".join(title.split()).strip("\"'“”‘’ ").rstrip(".") if isinstance(title, str) else ""
    words = text.split()
    letters = [c for c in text if c.isalpha()]
    first_line = " ".join(((caption or "").strip().split("\n") or [""])[0].split()).casefold()
    weird = (
        not 2 <= len(words) <= 10
        or len(text) > 100                       # ten words of a headline, not two very long ones
        or "#" in text or "@" in text or _URLISH.search(text)
        or any(unicodedata.category(c) == "So" or ord(c) >= 0x1F000 for c in text)
        or (len(letters) >= 4 and sum(c.isupper() for c in letters) > 0.7 * len(letters))
        or text.casefold().startswith(_TITLE_LEADS)
        or (first_line and text.casefold() == first_line)
    )
    return plain_title(caption) if weird else text


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
