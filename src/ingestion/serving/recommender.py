"""Chat-context retrieval over the event_inspirations vector store (Phase 6).

Turns a Discord chat message into a vibe query, searches the Chroma collection,
and applies spam controls before anything is shown. The ChromaSink is injected
so this is fully unit-testable offline (fake embedder + injected clock).

Spam prevention = intent gate (auto only) + per-channel cooldown (auto only) +
relevance-distance floor + recent-id dedup + max results.

Group planning (Phase 8): synthesize_request() + RecommendationService.plan()
read a whole multi-person chat transcript into a structured group request and
retrieve a shortlist, reusing the same ranking/spam logic.
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from src.ingestion.config import IngestionSettings

# Words that strip out as noise; what remains is the "vibe" we embed.
_STOPWORDS = {
    "a", "an", "the", "i", "im", "i'm", "we", "you", "to", "for", "of", "in", "on", "at",
    "is", "are", "do", "does", "can", "could", "would", "should", "any", "some", "good",
    "wanna", "want", "lets", "let's", "go", "going", "get", "got", "me", "us", "and", "or",
    "what", "whats", "what's", "where", "wheres", "where's", "should", "tonight", "today",
    "this", "that", "anyone", "someone", "know", "hey", "yo", "ok", "okay", "pls", "please",
}

# Auto-suggest intent gate: only react to messages that look like a request.
_TRIGGERS = {
    "eat", "eats", "food", "foodie", "hungry", "starving", "drink", "drinks", "bar", "bars",
    "coffee", "cafe", "brunch", "dinner", "lunch", "tacos", "taco", "bored",
    "plans", "plan", "where", "recommend", "rec", "recs", "suggestion", "suggestions",
    "vibe", "vibes", "do", "going", "hang", "hangout", "night", "tonight", "weekend",
    "event", "events", "music", "show", "popup", "pop-up", "market", "hike", "place", "spot",
}

_WORD = re.compile(r"[#a-z0-9'\-]+")
_MENTION = re.compile(r"<@!?\d+>")
_SLASH = re.compile(r"^\s*/\w+\s*")


def extract_query(text: str) -> str:
    """Strip mentions/command prefix/stopwords → compact vibe string for embedding."""
    text = _MENTION.sub(" ", text or "")
    text = _SLASH.sub("", text)
    tokens = _WORD.findall(text.lower())
    kept = [t for t in tokens if t.startswith("#") or t not in _STOPWORDS]
    return " ".join(kept).strip()


def looks_like_request(text: str) -> bool:
    """Intent gate for auto-suggest: does this message ask for a place/event?"""
    tokens = set(_WORD.findall((text or "").lower()))
    return bool(tokens & _TRIGGERS)


def synthesize_request(transcript: str, settings: IngestionSettings) -> dict:
    """LLM-synthesize a group's collective request from a chat transcript.

    Returns a dict with whatever of {vibe, area, budget, time} it could fill.
    Degrades to {} on any Ollama/parse failure — callers fall back to keyword
    extraction, so this never breaks /plan.
    """
    prompt = (
        "A group of friends is deciding where to go out together. From their chat below, "
        "infer what they COLLECTIVELY want and return ONLY a JSON object with these string keys:\n"
        '  "vibe"   - the food/drink/activity they want (short, e.g. "boba", "tacos", "live music")\n'
        '  "area"   - neighborhood or city if mentioned, else ""\n'
        '  "budget" - one of "cheap", "moderate", "fancy", or ""\n'
        '  "time"   - when, e.g. "tonight", "this weekend", else ""\n\n'
        f"Chat:\n{transcript}\n"
    )
    try:
        resp = httpx.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",  # force valid JSON output
            },
            timeout=settings.llm_timeout_s,
        )
        resp.raise_for_status()
        data = json.loads(resp.json().get("response", "{}"))
    except Exception:
        return {}
    out: dict = {}
    for key in ("vibe", "area", "budget", "time"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out


@dataclass
class Recommendation:
    content_hash: str
    venue_name: str
    category: str
    core_theme: str
    source_url: str
    distance: float
    start_epoch: Optional[int] = None
    end_epoch: Optional[int] = None
    schedule_status: Optional[str] = None


@dataclass
class RecommendationResult:
    recommendations: list[Recommendation] = field(default_factory=list)
    suppressed: bool = False
    reason: Optional[str] = None


@dataclass
class PlanResult:
    request: dict = field(default_factory=dict)        # {vibe, area, budget, time}
    recommendations: list[Recommendation] = field(default_factory=list)
    query: str = ""


class RecommendationService:
    def __init__(self, sink, settings: Optional[IngestionSettings] = None):
        self.sink = sink
        self.settings = settings or IngestionSettings()
        self._last_post: dict[str, float] = {}            # channel_id -> ts of last suggestion
        self._recent: dict[str, dict[str, float]] = {}    # channel_id -> {content_hash: ts}

    def recommend(
        self,
        channel_id: str,
        text: str,
        *,
        mode: str = "command",
        category: Optional[str] = None,
        now: Optional[float] = None,
    ) -> RecommendationResult:
        now = time.monotonic() if now is None else now
        auto = mode == "auto"

        # 1. Intent gate (auto only) — explicit /events always passes.
        if auto and not looks_like_request(text):
            return RecommendationResult(suppressed=True, reason="no request intent")

        # 2. Cooldown (auto only).
        if auto:
            last = self._last_post.get(channel_id)
            if last is not None and now - last < self.settings.rec_cooldown_s:
                return RecommendationResult(suppressed=True, reason="cooldown")

        # 3. Query.
        query = extract_query(text)
        if not query:
            return RecommendationResult(suppressed=True, reason="empty query")
        where = {"category": category} if category else None
        hits = self.sink.query(query, k=self.settings.rec_max_results * 2, where=where)

        # 4. Relevance floor + 5. dedup + max results.
        recent = self._recent.setdefault(channel_id, {})
        self._expire_recent(recent, now)
        picked: list[Recommendation] = []
        for hit in hits:
            if hit["distance"] > self.settings.rec_max_distance:
                continue
            rec = _to_recommendation(hit)
            if rec.content_hash in recent:
                continue
            picked.append(rec)
            if len(picked) >= self.settings.rec_max_results:
                break

        if not picked:
            return RecommendationResult(suppressed=True, reason="no relevant match")

        # 6. Commit spam-control state.
        self._last_post[channel_id] = now
        for rec in picked:
            recent[rec.content_hash] = now
        return RecommendationResult(recommendations=picked)

    def plan(self, channel_id: str, transcript: str, *, now: Optional[float] = None) -> "PlanResult":
        """Group-planning entry point: synthesize the group's request from a chat
        transcript, then retrieve a shortlist (reusing recommend's ranking/spam logic)."""
        request = synthesize_request(transcript, self.settings)
        query = " ".join(v for v in (request.get("vibe"), request.get("area")) if v).strip()
        if not query:
            query = extract_query(transcript)        # fallback: keyword vibe over the whole thread
        result = self.recommend(channel_id, query, mode="command", now=now)
        return PlanResult(request=request, recommendations=result.recommendations, query=query)

    def _expire_recent(self, recent: dict[str, float], now: float) -> None:
        window = self.settings.rec_dedup_window_s
        for key in [h for h, ts in recent.items() if now - ts > window]:
            del recent[key]


def _to_recommendation(hit: dict) -> Recommendation:
    m = hit.get("metadata", {})
    return Recommendation(
        content_hash=m.get("content_hash", ""),
        venue_name=m.get("venue_name", "Unknown venue"),
        category=m.get("category", "other"),
        core_theme=m.get("core_theme", ""),
        source_url=m.get("source_url", ""),
        distance=hit.get("distance", 1.0),
        start_epoch=m.get("start_epoch"),
        end_epoch=m.get("end_epoch"),
        schedule_status=m.get("schedule_status"),
    )
