"""Chat-context retrieval over the event_inspirations vector store (Phase 6).

Turns a Discord chat message into a vibe query, searches the Chroma collection,
and applies spam controls before anything is shown. The ChromaSink is injected
so this is fully unit-testable offline (fake embedder + injected clock).

Spam prevention = intent gate (auto only) + per-channel cooldown (auto only) +
relevance-distance floor + recent-id dedup + max results.

Group planning (Phase 8): synthesize_request() + RecommendationService.plan()
read a whole multi-person chat transcript into a structured group request and
retrieve a shortlist, reusing the same ranking/spam logic.

ML layers (Phase 9):
  1. User taste profile — weight query toward spots the user previously liked.
  2. LLM cross-encoder re-ranking — after vector search, ask the LLM to score
     each (query, spot) pair and reorder; much more precise than cosine alone.
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger("ingestion.recommender")

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


# ── Location awareness ───────────────────────────────────────────────────────
# "i'm at 2nd street", "we're in downtown LB", "near the pike" → an origin
# point; suggestions are then ranked by real distance. In /plan, several
# people saying where they are → the midpoint (the heritage negotiator's
# fairness trick, src/services/location_service.py::get_midpoint).

_PLACE = r"[A-Za-z0-9' .&-]{3,40}"
_ORIGIN_RE = re.compile(
    rf"(?i)\b(?:i'?m|im|we'?re|were)\s+(?:at|in|near|around)\s+(?P<place>{_PLACE})"
    rf"|\b(?:near|around)\s+(?P<place2>{_PLACE})"
)
_USER_LOC_RE = re.compile(
    rf"(?im)^\s*(?P<name>[^:\n]{{1,40}}?)\s*:\s*.*?\b(?:i'?m|im|we'?re)\s+(?:at|in|near)\s+(?P<place>{_PLACE})"
)
_TRAILING_NOISE = {
    "tonight", "today", "tomorrow", "rn", "now", "later", "tho", "though",
    "pls", "please", "btw", "lol", "haha", "so", "and",
}


def _clean_place(raw: Optional[str]) -> Optional[str]:
    tokens = (raw or "").strip().split()
    while tokens and tokens[-1].lower().strip(".!?,") in _TRAILING_NOISE:
        tokens.pop()
    place = " ".join(tokens[:5]).strip(" .")
    return place if len(place) >= 3 else None


def extract_origin_text(text: str) -> Optional[str]:
    """The place phrase in a request, or None ("im at X", "near X")."""
    match = _ORIGIN_RE.search(text or "")
    if not match:
        return None
    return _clean_place(match.group("place") or match.group("place2"))


def user_locations(transcript: str) -> dict[str, str]:
    """{speaker: place} for every 'name: … i'm at X' transcript line."""
    out: dict[str, str] = {}
    for match in _USER_LOC_RE.finditer(transcript or ""):
        place = _clean_place(match.group("place"))
        if place:
            out[match.group("name").strip()] = place
    return out


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    rlat1, rlng1, rlat2, rlng2 = map(radians, (lat1, lng1, lat2, lng2))
    a = sin((rlat2 - rlat1) / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin((rlng2 - rlng1) / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(a))


def _default_geocode(place: str):
    """Nominatim via the repo's existing helper (1s politeness delay built in)."""
    from src.services.location_service import address_to_coords

    return address_to_coords(place)


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
    lat: Optional[float] = None
    lng: Optional[float] = None
    distance_km: Optional[float] = None    # real-world distance from the user's origin
    pinned: bool = False                   # the chat mentioned this spot by name
    attended: bool = False                 # the group actually went (went-there loop)


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


# ── ML Layer 1: User taste profile ───────────────────────────────────────────

def get_user_liked_embeddings(sink, user_id: str) -> list[list[float]]:
    """Return raw embedding vectors for all spots this user voted 👍 on.

    ChromaDB metadata stores voters as a JSON string {"user_id": "display_name"}.
    We scan all metadata entries, find the ones where user_id is in voters, then
    retrieve their stored embeddings from the collection.
    """
    if not user_id:
        return []
    try:
        collection = getattr(sink, "collection", None)
        if collection is None:
            return []
        res = collection.get(include=["metadatas", "embeddings", "ids"])
        liked_ids = []
        for doc_id, meta in zip(res.get("ids", []), res.get("metadatas", []) or []):
            voters = json.loads((meta or {}).get("voters", "{}"))
            if user_id in voters:
                liked_ids.append(doc_id)
        if not liked_ids:
            return []
        liked = collection.get(ids=liked_ids, include=["embeddings"])
        return liked.get("embeddings", []) or []
    except Exception as exc:
        log.debug("taste profile fetch failed: %s", exc)
        return []


def blend_query_with_taste(
    query_embedding: list[float],
    liked_embeddings: list[list[float]],
    alpha: float = 0.25,
) -> list[float]:
    """Blend query vector with the user's average taste profile.

    alpha=0.25 means 75% what they asked for + 25% what they usually like.
    Pure query search at alpha=0.0; pure taste at alpha=1.0.
    Returns original query if no liked embeddings (new users, cold start).
    """
    if not liked_embeddings:
        return query_embedding
    n = len(query_embedding)
    taste = [
        sum(emb[i] for emb in liked_embeddings) / len(liked_embeddings)
        for i in range(n)
    ]
    blended = [(1 - alpha) * q + alpha * t for q, t in zip(query_embedding, taste)]
    # L2-normalise so cosine distance stays meaningful after blending
    mag = sum(x * x for x in blended) ** 0.5
    return [x / mag for x in blended] if mag > 0 else blended


# ── ML Layer 2: LLM cross-encoder re-ranking ────────────────────────────────

def rerank_with_llm(
    query: str,
    candidates: list[dict],
    settings,
) -> list[dict]:
    """Re-score (query, spot) pairs with the LLM and return them reordered.

    ChromaDB cosine similarity finds *similar* spots; the LLM understands
    *relevant* ones. Sending 6 candidates and asking for an ordered list costs
    one Ollama call (~2s on CPU) but measurably improves /plan precision.

    Falls back to the original order on any failure (timeout, parse error, etc.)
    so /plan never breaks from this step.
    """
    if len(candidates) <= 1:
        return candidates
    try:
        items = []
        for i, c in enumerate(candidates):
            m = c.get("metadata", {})
            items.append(
                f'{i}. {m.get("venue_name", "?")} — {m.get("core_theme", "")} '
                f'({m.get("category", "")})'
            )
        spots_text = "\n".join(items)
        prompt = (
            f'A group wants: "{query}"\n\n'
            f"Rank these spots from BEST to WORST match for what they want.\n"
            f"Return ONLY a JSON array of the original numbers in ranked order, "
            f"e.g. [2, 0, 4, 1, 3]. No explanation.\n\n"
            f"Spots:\n{spots_text}\n"
        )
        resp = httpx.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
            timeout=settings.llm_timeout_s,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "[]")
        # The model sometimes wraps the array in {"ranked": [...]}
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            parsed = next(
                (v for v in parsed.values() if isinstance(v, list)), None
            ) or []
        ranked_indices = [int(i) for i in parsed if isinstance(i, (int, float))]
        # Build reordered list; append any candidates not mentioned by the LLM
        seen = set()
        reordered = []
        for idx in ranked_indices:
            if 0 <= idx < len(candidates) and idx not in seen:
                reordered.append(candidates[idx])
                seen.add(idx)
        for idx, c in enumerate(candidates):
            if idx not in seen:
                reordered.append(c)
        return reordered
    except Exception as exc:
        log.debug("rerank failed, keeping original order: %s", exc)
        return candidates


class RecommendationService:
    def __init__(self, sink, settings: Optional[IngestionSettings] = None, geocode_fn=None):
        self.sink = sink
        self.settings = settings or IngestionSettings()
        # Injectable geocoder for offline tests; defaults to Nominatim lazily.
        self.geocode_fn = geocode_fn
        self._geo_cache: dict[str, Optional[tuple[float, float]]] = {}
        self._last_post: dict[str, float] = {}            # channel_id -> ts of last suggestion
        self._recent: dict[str, dict[str, float]] = {}    # channel_id -> {content_hash: ts}

    def _geocode(self, place: Optional[str]) -> Optional[tuple[float, float]]:
        if not place:
            return None
        key = place.lower().strip()
        if key not in self._geo_cache:
            try:
                lat, lng = (self.geocode_fn or _default_geocode)(place)
            except Exception:
                lat = lng = None
            self._geo_cache[key] = (
                (float(lat), float(lng)) if lat is not None and lng is not None else None
            )
        return self._geo_cache[key]

    def recommend(
        self,
        channel_id: str,
        text: str,
        *,
        mode: str = "command",
        category: Optional[str] = None,
        now: Optional[float] = None,
        origin: Optional[tuple[float, float]] = None,
        user_id: str = "",          # ML Layer 1: personalise by voter history
        rerank: bool = True,        # ML Layer 2: LLM cross-encoder re-ranking
    ) -> RecommendationResult:
        now = time.monotonic() if now is None else now
        auto = mode == "auto"

        # Location awareness: "im near X" in the request → rank by real distance.
        if origin is None:
            origin = self._geocode(extract_origin_text(text))

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
        # ML Layer 1: blend query with user's taste profile (25% personal, 75% query).
        # Works by computing the average embedding of all spots the user voted 👍,
        # then nudging the query vector toward that direction before ChromaDB search.
        fetch_k = self.settings.rec_max_results * 4 if rerank else self.settings.rec_max_results * 2
        liked_embeddings = get_user_liked_embeddings(self.sink, user_id)
        if liked_embeddings and hasattr(self.sink, "embedder"):
            try:
                raw_query_emb = self.sink.embedder([query])[0]
                blended_emb = blend_query_with_taste(raw_query_emb, liked_embeddings)
                # Query directly with the blended vector instead of the text
                hits = self.sink.collection.query(
                    query_embeddings=[blended_emb],
                    n_results=fetch_k,
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
                hits = [
                    {"document": doc, "metadata": meta, "distance": dist}
                    for doc, meta, dist in zip(
                        hits["documents"][0], hits["metadatas"][0], hits["distances"][0]
                    )
                ]
                log.info("🎯 taste profile applied — %d liked spots biasing query (α=0.25)", len(liked_embeddings))
            except Exception as exc:
                log.warning("taste blend failed, falling back to text query: %s", exc)
                hits = self.sink.query(query, k=fetch_k, where=where)
        else:
            if user_id:
                log.info("👤 user %s has no liked spots yet — cold start, using raw query", user_id)
            hits = self.sink.query(query, k=fetch_k, where=where)

        # ML Layer 2: LLM cross-encoder re-ranking.
        # After vector search returns candidates, ask llama3.2 to score each
        # (query, spot) pair and reorder by relevance — much more precise than
        # cosine distance alone. Only fires for command/plan mode (not auto-suggest)
        # to avoid adding latency to every message.
        if rerank and not auto and len(hits) > 1:
            log.info("🔁 LLM re-ranking %d candidates for query: %r", len(hits), query[:60])
            hits = rerank_with_llm(query, hits, self.settings)
            log.info("✅ re-ranking complete — top result: %s", hits[0]["metadata"].get("venue_name", "?") if hits else "none")

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

        # 5.5 Distance annotation + nearest-first (spots without coords sink last).
        if origin is not None:
            for rec in picked:
                if rec.lat is not None and rec.lng is not None:
                    rec.distance_km = round(
                        _haversine_km(origin[0], origin[1], rec.lat, rec.lng), 1
                    )
            picked.sort(
                key=lambda r: (r.distance_km is None, r.distance_km or 0.0, r.distance)
            )

        # 6. Commit spam-control state.
        self._last_post[channel_id] = now
        for rec in picked:
            recent[rec.content_hash] = now
        return RecommendationResult(recommendations=picked)

    def plan(self, channel_id: str, transcript: str, *, now: Optional[float] = None, user_id: str = "") -> "PlanResult":
        """Group-planning entry point: synthesize the group's request from a chat
        transcript, then retrieve a shortlist (reusing recommend's ranking/spam logic)."""
        request = synthesize_request(transcript, self.settings)
        query = " ".join(v for v in (request.get("vibe"), request.get("area")) if v).strip()
        if not query:
            query = extract_query(transcript)        # fallback: keyword vibe over the whole thread

        # Fairness: people who said where they are pull the results toward their
        # midpoint; one person → near them; else the group's stated area.
        origin: Optional[tuple[float, float]] = None
        located: dict[str, tuple[tuple[float, float], str]] = {}
        for name, place in user_locations(transcript).items():
            point = self._geocode(place)
            if point is not None:
                located[name] = (point, place)
        if len(located) >= 2:
            points = [entry[0] for entry in located.values()]
            origin = (
                sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points),
            )
            request["midpoint_of"] = " + ".join(
                f"{name} ({entry[1]})" for name, entry in located.items()
            )
        elif len(located) == 1:
            ((point, place),) = located.values()
            origin = point
            request["near"] = place
        elif request.get("area"):
            origin = self._geocode(request["area"])

        result = self.recommend(channel_id, query, mode="command", now=now, origin=origin, user_id=user_id)

        # Name pinning: a saved spot mentioned by name in the chat ("casa loma
        # was so nice") leads the shortlist, marked as the group's own pick.
        pinned = self._pin_mentions(transcript)
        pinned_ids = {rec.content_hash for rec in pinned}
        others = [rec for rec in result.recommendations if rec.content_hash not in pinned_ids]
        # Places the group actually went (went-there loop) outrank never-tried
        # ones — "that place was nice" energy, without needing the name.
        others.sort(key=lambda r: not r.attended)
        return PlanResult(request=request, recommendations=pinned + others, query=query)

    def _pin_mentions(self, transcript: str) -> list[Recommendation]:
        """Catalog spots whose venue name literally appears in the chat."""
        collection = getattr(self.sink, "collection", None)
        if collection is None:
            return []
        try:
            res = collection.get(include=["metadatas"])
        except Exception:
            return []
        text = (transcript or "").lower()
        pinned: list[Recommendation] = []
        for meta in res.get("metadatas") or []:
            meta = meta or {}
            name = str(meta.get("venue_name", "")).strip()
            if len(name) >= 4 and name.lower() in text:
                rec = _to_recommendation({"metadata": meta, "distance": 0.0})
                rec.pinned = True
                pinned.append(rec)
        return pinned[: self.settings.rec_max_results]

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
        lat=m.get("lat"),
        lng=m.get("lng"),
        attended=bool(m.get("attended")),
    )
