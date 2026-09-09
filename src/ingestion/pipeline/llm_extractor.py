"""Local-LLM field extraction with a strict, fail-safe response ladder.

The model only ever fills LlmExtraction (see schemas/extraction.py). This module
turns a clean payload into a validated LlmExtraction, or returns None so the
caller falls back to Phase 1 heuristics. It never raises on a bad model
response and never lets the model bypass validation.

Determinism: temperature 0 + fixed seed + Ollama's grammar-constrained `format`
decoding → the same payload yields the same JSON.

Transport is injectable (a callable taking (messages, format_schema) -> str) so
the whole ladder is unit-testable offline without a running Ollama.
"""

import json
import logging
import re
from typing import Callable, Optional

import httpx
from pydantic import ValidationError

from src.ingestion.config import IngestionSettings
from src.ingestion.schemas.extraction import CategoryLiteral, LlmExtraction

log = logging.getLogger("ingestion.llm")

Transport = Callable[[list[dict], dict], str]

SYSTEM_PROMPT = """You are a strict data-extraction engine inside a local pipeline. You are not a chatbot.
You receive a JSON object containing text fragments scraped from one public social media
post (caption, title, location tag, hashtags) and, when available, a `transcript` of the
video's spoken audio. The transcript is a valid source for venue_name/location — a place
that is only *said* in the video still counts as appearing in the input. Extract
event-inspiration fields.

Rules:
1. Output ONLY a JSON object matching the schema you were given. No prose, no markdown.
2. NEVER invent information. venue_name and raw_location_text must appear in the input
   text (you may fix casing/spacing). If no venue is named, venue_name is null.
3. If the post has no explicit location: raw_location_text = null, place_names = [].
   Do not guess a city from vibes or hashtag fragments you are unsure about.
4. If the post is vague or not about a place/food/event at all: is_vague = true,
   category = "other", core_theme = a short literal summary of whatever text exists.
5. candidate_times: copy time/date phrases verbatim as they appear ("this Friday", "8pm",
   "June 5"). Never resolve them to absolute dates — a downstream parser does that.
6. category: choose the single best fit; "other" when unsure. A pop-up/market beats the
   food it serves; a venue's regular menu post is food_drink, not an event.
7. today_date in the input is context only — never copy it into candidate_times."""

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

# When not using schema-constrained decoding, the model needs the field list
# spelled out (the `format` schema used to carry it implicitly).
_SCHEMA_HINT = (
    "\n\nReturn ONLY a JSON object with exactly these keys:\n"
    '  "venue_name": string or null   — the specific named place, verbatim from the input\n'
    '  "core_theme": string or null   — one short phrase describing the post\n'
    '  "category": one of "food_drink","cafe_dessert","nightlife","live_music",'
    '"market_popup","outdoors","community","other"\n'
    '  "raw_location_text": string or null — location as written in the post\n'
    '  "place_names": array of strings — neighbourhoods/cities/venues mentioned (may be empty)\n'
    '  "candidate_times": array of strings — raw date/time phrases, verbatim (may be empty)\n'
    '  "is_vague": boolean — true when the post is not clearly about a place/event'
)


class LlmFieldExtractor:
    def __init__(self, settings: IngestionSettings, transport: Optional[Transport] = None):
        self.settings = settings
        self.transport = transport or self._default_transport
        self.format_schema = LlmExtraction.ollama_format_schema()

    @property
    def model(self) -> str:
        return self.settings.ollama_model

    # ── default Ollama transport ─────────────────────────────────────────────

    def _default_transport(self, messages: list[dict], format_schema: dict) -> str:
        """POST to Ollama /api/chat. `format` is the full JSON schema only when
        llm_strict_schema is set (grammar-constrained: exact but slow on CPU);
        otherwise "json" (valid-JSON mode). The parse ladder validates either way."""
        fmt = format_schema if self.settings.llm_strict_schema else "json"
        resp = httpx.post(
            f"{self.settings.ollama_url}/api/chat",
            json={
                "model": self.settings.ollama_model,
                "messages": messages,
                "stream": False,
                "format": fmt,
                "options": {"temperature": 0, "seed": self.settings.llm_seed},
            },
            timeout=self.settings.llm_timeout_s,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    # ── public API ───────────────────────────────────────────────────────────

    def extract(self, payload: dict) -> Optional[LlmExtraction]:
        """Return a cross-checked LlmExtraction, or None to fall back to heuristics."""
        payload_text = _payload_text(payload)
        system = SYSTEM_PROMPT if self.settings.llm_strict_schema else SYSTEM_PROMPT + _SCHEMA_HINT
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

        last_error: Optional[str] = None
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                content = self.transport(messages, self.format_schema)
            except Exception as exc:
                # Ollama down/timeout is not a pipeline failure — degrade quietly.
                log.warning(f"[llm] transport error ({type(exc).__name__}: {exc}); using heuristics")
                return None

            parsed, error = self._parse(content)
            if parsed is not None:
                return self._strip_hallucinations(parsed, payload_text)

            last_error = error
            log.info(f"[llm] response failed validation (attempt {attempt + 1}): {error}")
            messages = messages + [
                {"role": "assistant", "content": content[:2000]},
                {
                    "role": "user",
                    "content": (
                        f"Your previous response failed schema validation: {error}. "
                        "Return ONLY a corrected JSON object matching the schema. "
                        "No prose, no markdown."
                    ),
                },
            ]

        log.warning(
            f"[llm] giving up after {self.settings.llm_max_retries + 1} attempt(s) "
            f"({last_error}); using heuristics"
        )
        return None

    # ── response ladder internals ─────────────────────────────────────────────

    def _parse(self, content: str) -> tuple[Optional[LlmExtraction], Optional[str]]:
        """Step 1 strict decode → step 2 repair (fences / first JSON object)."""
        try:
            return LlmExtraction.model_validate_json(content), None
        except ValidationError as first_error:
            block = _first_json_object(_FENCE.sub("", content))
            if block is not None:
                try:
                    return LlmExtraction.model_validate_json(block), None
                except ValidationError as repaired_error:
                    return None, _summarize(repaired_error)
            return None, _summarize(first_error)

    def _strip_hallucinations(self, ext: LlmExtraction, payload_text: str) -> LlmExtraction:
        """Enforce prompt rule 2 in code: discard fields not grounded in the input."""
        haystack = _collapse(payload_text)

        if ext.venue_name and _collapse(ext.venue_name) not in haystack:
            log.info(f"[llm] discarding ungrounded venue_name {ext.venue_name!r}")
            ext.venue_name = None
        if ext.raw_location_text and _collapse(ext.raw_location_text) not in haystack:
            log.info(f"[llm] discarding ungrounded raw_location_text {ext.raw_location_text!r}")
            ext.raw_location_text = None
        ext.place_names = [p for p in ext.place_names if p.strip() and _collapse(p) in haystack]
        if ext.category not in CategoryLiteral:
            ext.category = "other"
        return ext


# ── module helpers ─────────────────────────────────────────────────────────


def _payload_text(payload: dict) -> str:
    parts: list[str] = []
    for key in ("caption", "transcript", "title", "og_description", "location_text"):
        value = payload.get(key)
        if value:
            parts.append(str(value))
    tags = payload.get("hashtags")
    if tags:
        parts.append(" ".join(tags))
    return " ".join(parts)


def _collapse(text: str) -> str:
    """Alphanumeric-only, lowercased. Grounding on this lets a venue the model
    legitimately re-spaced ('@abouttimecafe' -> 'About Time Cafe') still match the
    source text — which the prompt expressly permits ('you may fix casing/spacing')
    — while a genuinely invented name still won't appear."""
    return _NON_ALNUM.sub("", text.lower())


def _summarize(exc: ValidationError) -> str:
    joined = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
    return joined[:500]


def _first_json_object(text: str) -> Optional[str]:
    """Return the first balanced {...} block, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
