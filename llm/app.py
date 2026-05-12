"""
Container 2 -- LLM Service
- On startup: verifies connectivity to the DB service and to Ollama (or OpenAI).
- Pulls conversation history from the DB service.
- Builds a prompt and calls Ollama (llama3, gemma3:4b, ...) or OpenAI (gpt-*).
- Parses any structured actions the model returns (e.g. create_event).
- Persists the assistant reply back to the DB service.

Model routing (set OLLAMA_MODEL in .env):
  llama3.2:latest  -> Ollama  (default)
  gemma3:4b        -> Ollama
  gpt-4o           -> OpenAI  (requires OPENAI_API_KEY)
  gpt-5.4-mini      -> OpenAI  (requires OPENAI_API_KEY)
"""

import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import date

# -- Logging setup -----------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s -- %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("llm-service")

# -- Config ------------------------------------------------------------------
OLLAMA_URL     = os.getenv("OLLAMA_URL",    "http://host.docker.internal:11434")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL",  "llama3.2:latest").strip()
DB_URL         = os.getenv("DB_URL",        "http://db:8002")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_URL     = "https://api.openai.com/v1/chat/completions"

# Route to OpenAI if the model name looks like a GPT model
USE_OPENAI = OLLAMA_MODEL.lower().startswith("gpt-")


# -- Startup / shutdown ------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run connectivity checks before accepting traffic."""
    log.info("=" * 60)
    log.info("LLM Service starting up")
    log.info(f"  Model   : {OLLAMA_MODEL}")
    log.info(f"  Backend : {'OpenAI' if USE_OPENAI else f'Ollama @ {OLLAMA_URL}'}")
    log.info(f"  DB      : {DB_URL}")
    log.info("=" * 60)

    async with httpx.AsyncClient(timeout=5.0) as client:

        # -- Check DB --------------------------------------------------------
        try:
            r = await client.get(f"{DB_URL}/health")
            log.info(f"[startup] DB ok: {r.json()}")
        except Exception as exc:
            log.warning(f"[startup] DB not reachable -- will retry per-request. ({exc})")

        # -- Check LLM backend -----------------------------------------------
        if USE_OPENAI:
            if not OPENAI_API_KEY:
                log.error("[startup] OPENAI_API_KEY is not set! OpenAI calls will fail.")
            else:
                try:
                    r = await client.get(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    )
                    if r.status_code == 200:
                        log.info(f"[startup] OpenAI API key valid (model={OLLAMA_MODEL})")
                    else:
                        log.warning(f"[startup] OpenAI responded {r.status_code}: {r.text[:200]}")
                except Exception as exc:
                    log.warning(f"[startup] Could not reach OpenAI: {exc}")
        else:
            # Ollama: list available models and highlight the active one
            try:
                r = await client.get(f"{OLLAMA_URL}/api/tags")
                r.raise_for_status()
                models_raw = r.json().get("models", [])
                available  = [m["name"] for m in models_raw]

                log.info(f"[startup] Ollama reachable at {OLLAMA_URL}")
                if models_raw:
                    log.info(f"[startup]   {'Model':<35} {'Size':>8}   Modified")
                    log.info(f"[startup]   {'-'*35} {'-'*8}   {'-'*10}")
                    for m in models_raw:
                        size_gb  = m.get("size", 0) / 1_000_000_000
                        modified = (m.get("modified_at") or "")[:10]
                        tag      = "  <-- active" if m["name"] == OLLAMA_MODEL else ""
                        log.info(f"[startup]   {m['name']:<35} {size_gb:>7.1f}G   {modified}{tag}")
                else:
                    log.warning("[startup]   No models found -- run: ollama pull llama3.2:latest")

                if OLLAMA_MODEL not in available:
                    log.warning(
                        f"[startup] Model '{OLLAMA_MODEL}' not found in Ollama! "
                        f"Run: ollama pull {OLLAMA_MODEL}"
                    )
                else:
                    log.info(f"[startup] Active model '{OLLAMA_MODEL}' is ready")

            except httpx.ConnectError:
                log.error(
                    f"[startup] Cannot connect to Ollama at {OLLAMA_URL}. "
                    "Make sure Ollama is running on the host and the container has "
                    "'extra_hosts: host.docker.internal:host-gateway' set."
                )
            except Exception as exc:
                log.error(f"[startup] Ollama check failed: {exc}")

    log.info("LLM Service ready -- accepting requests")
    yield
    log.info("LLM Service shutting down")


# -- App ---------------------------------------------------------------------

app = FastAPI(title="Event Planner LLM", lifespan=lifespan)


# -- Per-request logging middleware ------------------------------------------

class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = uuid.uuid4().hex[:8]
        t0 = time.perf_counter()
        log.info(f"[{req_id}] --> {request.method} {request.url.path}")
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            log.error(f"[{req_id}] Unhandled exception: {exc}")
            raise
        elapsed = (time.perf_counter() - t0) * 1000
        log.info(f"[{req_id}] <-- {response.status_code} ({elapsed:.0f} ms)")
        return response


app.add_middleware(RequestLogMiddleware)


# -- System prompt -----------------------------------------------------------

SYSTEM_PROMPT = """You are Eve -- a friendly event coordination assistant living inside a Discord server for a group of friends.

Your job:
* Help the group brainstorm, schedule, and keep track of events (game nights, outings, trips, etc.).
* Remember previous conversations in the channel so you can reference them naturally.
* Be concise and conversational -- you're chatting with friends, not writing a report.

Location awareness:
* You may be given known member locations below. Use these to suggest fair meeting spots (roughly central to everyone).
* If no locations are known, ask the group where they're coming from so you can suggest a fair spot.
* If REAL VENUES are provided, you MUST list them explicitly to the user (e.g. "Here are 3 highly-rated spots near the midpoint: 1. [Venue A], 2. [Venue B]. Which one looks good?") and wait for them to choose before creating the event.

Creating events:
When a user asks you to schedule or create an event, respond naturally AND you MUST append a machine-readable action block at the very end of your reply (after your text, on its own line).

Here is the EXACT format — copy this structure precisely:

<action>{"type": "create_event", "name": "Game Night", "description": "Friday gaming session", "location": "Dave & Busters, Lakewood", "start_time": "2025-06-15T19:00:00", "end_time": "2025-06-15T21:00:00"}</action>

Rules for the action block:
- You MUST include the <action>...</action> tags exactly as shown. This is critical.
- start_time and end_time must be valid ISO 8601 datetimes (no timezone suffix needed).
- If the user doesn't mention a duration, default end_time to 2 hours after start_time.
- If the user doesn't mention a specific time, pick something reasonable and tell them what you chose.
- Keep "name" short (Discord event title limit is 100 chars).
- Include a "location" field with a suggested venue or area.
- Only emit the block when actually creating an event -- not for general questions.
- The action block must be the LAST thing in your reply, on its own line.
"""

ACTION_PATTERN = re.compile(r"<action>(.*?)</action>", re.DOTALL)


def fallback_parse_action(text: str) -> dict | None:
    """Try to extract a create_event JSON from LLM output even if <action> tags are missing.

    Small models often forget the tags, use markdown code blocks, or vary casing.
    This catches those cases.
    """
    # Try various tag patterns: <Action>, [action], {action}, etc.
    for pattern in [
        re.compile(r'<[Aa]ction>(.*?)</[Aa]ction>', re.DOTALL),
        re.compile(r'\[action\](.*?)\[/action\]', re.DOTALL),
        re.compile(r'```(?:json)?\s*(\{.*?"type"\s*:\s*"create_event".*?\})\s*```', re.DOTALL),
    ]:
        m = pattern.search(text)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                continue

    # Last resort: find any JSON object with "create_event" in it
    json_pattern = re.compile(r'(\{[^{}]*"type"\s*:\s*"create_event"[^{}]*\})', re.DOTALL)
    m = json_pattern.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    return None


# -- Schema ------------------------------------------------------------------

class ChatRequest(BaseModel):
    channel_id: str
    guild_id:   str
    user_id:    str
    username:   str
    message:    str


# -- LLM call helpers --------------------------------------------------------

async def call_ollama(client: httpx.AsyncClient, messages: list[dict]) -> str:
    log.info(f"[ollama] Sending {len(messages)} messages to '{OLLAMA_MODEL}'")
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": messages, "stream": False},
            timeout=120.0,
        )
        resp.raise_for_status()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"Cannot reach Ollama at {OLLAMA_URL}: {exc}")
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Ollama error {exc.response.status_code}: {exc.response.text[:300]}")
    elapsed = (time.perf_counter() - t0) * 1000
    content = resp.json()["message"]["content"]
    log.info(f"[ollama] Response in {elapsed:.0f} ms -- {len(content)} chars")
    return content


async def call_openai(client: httpx.AsyncClient, messages: list[dict]) -> str:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")
    log.info(f"[openai] Sending {len(messages)} messages to '{OLLAMA_MODEL}'")
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={"model": OLLAMA_MODEL, "messages": messages},
            timeout=60.0,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI error {exc.response.status_code}: {exc.response.text[:300]}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI connection error: {exc}")
    elapsed = (time.perf_counter() - t0) * 1000
    content = resp.json()["choices"][0]["message"]["content"]
    log.info(f"[openai] Response in {elapsed:.0f} ms -- {len(content)} chars")
    return content


# -- Main chat endpoint ------------------------------------------------------

@app.post("/chat")
async def chat(req: ChatRequest):
    log.info(
        f"[chat] guild={req.guild_id} channel={req.channel_id} "
        f"user={req.username!r} | {req.message[:120]!r}"
    )

    async with httpx.AsyncClient(timeout=180.0) as client:

        # 1. Fetch conversation history
        try:
            hist_resp = await client.get(f"{DB_URL}/messages/{req.channel_id}?limit=20")
            history = hist_resp.json() if hist_resp.status_code == 200 else []
            log.debug(f"[db] {len(history)} history messages for channel {req.channel_id}")
        except Exception as exc:
            log.warning(f"[db] Could not fetch history: {exc} -- proceeding without it")
            history = []

        # 2. Fetch saved member locations for this guild
        location_context = ""
        try:
            loc_resp = await client.get(f"{DB_URL}/locations/{req.guild_id}")
            if loc_resp.status_code == 200:
                locations = loc_resp.json()
                if locations:
                    loc_lines = [f"  - {loc['display_name']}: {loc['location_text']}" for loc in locations]
                    location_context = "\n\nKnown member locations:\n" + "\n".join(loc_lines) + "\n"
                    log.info(f"[locations] Injecting {len(locations)} member location(s) into prompt")
                    
                    # Step 2b: Centroid Math & Overpass Venue Scraping
                    valid_coords = [(loc['lat'], loc['lng']) for loc in locations if loc.get('lat') is not None and loc.get('lng') is not None]
                    if len(valid_coords) >= 1:
                        avg_lat = sum(c[0] for c in valid_coords) / len(valid_coords)
                        avg_lng = sum(c[1] for c in valid_coords) / len(valid_coords)
                        
                        # 2b-1: Snap Centroid to Nearest Populated City
                        try:
                            rev_resp = await client.get(
                                "https://nominatim.openstreetmap.org/reverse",
                                params={'lat': avg_lat, 'lon': avg_lng, 'format': 'json', 'zoom': 10},
                                headers={'User-Agent': 'SocialAgent-Team10-StudentProject'},
                                timeout=10.0
                            )
                            rev_data = rev_resp.json()
                            if 'error' not in rev_data and 'lat' in rev_data:
                                avg_lat = float(rev_data['lat'])
                                avg_lng = float(rev_data['lon'])
                                city_name = rev_data.get('name', 'Unknown')
                                log.info(f"[osm] Snapped centroid to nearest city: {city_name} ({avg_lat:.4f}, {avg_lng:.4f})")
                        except Exception as e:
                            log.warning(f"[osm] Failed to snap to nearest city: {e}")
                        
                        # Redundancy Strategy:
                        # 1. Try Centroid (Dynamic Radius)
                        # 2. Try User 1's City
                        # 3. Try User 2's City
                        
                        search_points = []
                        # Point 1: The Centroid (Snapped)
                        search_points.append({"lat": avg_lat, "lng": avg_lng, "label": "Centroid"})
                        
                        # Point 2 & 3: User Home Cities (Fallbacks)
                        for loc in locations[:2]:
                            if loc.get('lat') and loc.get('lng'):
                                search_points.append({"lat": loc['lat'], "lng": loc['lng'], "label": loc['display_name']})
                        
                        found_venues = []
                        for point in search_points:
                            lat, lng = point['lat'], point['lng']
                            
                            for radius in [5000, 15000, 30000]:
                                try:
                                    log.info(f"[osm] Trying {point['label']} at {radius}m...")
                                    overpass_query = f'[out:json];(node["amenity"~"cafe|bar|restaurant"](around:{radius},{lat},{lng}););out 20;'
                                    osm_resp = await client.post(
                                        "https://overpass-api.de/api/interpreter", 
                                        data={'data': overpass_query}, 
                                        headers={'User-Agent': 'SocialAgent-Team10-StudentProject'},
                                        timeout=10.0
                                    )
                                    
                                    if osm_resp.status_code == 200:
                                        osm_data = osm_resp.json()
                                        for el in osm_data.get('elements', []):
                                            tags = el.get('tags', {})
                                            name = tags.get('name')
                                            cuisine = tags.get('cuisine', '')
                                            if name:
                                                label = f"{name} ({cuisine.title()})" if cuisine else name
                                                if label not in found_venues:
                                                    found_venues.append(label)
                                            if len(found_venues) >= 3: break
                                    
                                    if found_venues: break
                                    
                                    # Secondary Redundancy: Try Nominatim if Overpass is empty or slow
                                    if not found_venues:
                                        nom_resp = await client.get(
                                            "https://nominatim.openstreetmap.org/search",
                                            params={'q': 'restaurant', 'format': 'json', 'lat': lat, 'lon': lng, 'limit': 3},
                                            headers={'User-Agent': 'SocialAgent-Team10-StudentProject'}
                                        )
                                        if nom_resp.status_code == 200:
                                            for item in nom_resp.json():
                                                name = item.get('display_name', '').split(',')[0]
                                                if name and name not in found_venues:
                                                    found_venues.append(name)
                                        if found_venues: break

                                except Exception as e:
                                    log.warning(f"[osm] Search failed for {point['label']} at {radius}m: {e}")
                                    continue
                            
                            if found_venues:
                                venue_str = ", ".join(found_venues)
                                if point['label'] == "Centroid":
                                    location_context += f"\nREAL VENUES NEAR EXACT MIDPOINT: {venue_str}\n"
                                else:
                                    location_context += f"\nCOULD NOT FIND MIDPOINT VENUES. SUGGESTING SPOTS NEAR {point['label']}: {venue_str}\n"
                                
                                location_context += "(You MUST pick one of these real venues if suggesting a spot!)\n"
                                log.info(f"[osm] Found venues near {point['label']}: {venue_str}")
                                break # Stop completely once we have venues

        except Exception as exc:
            log.warning(f"[db] Could not fetch locations: {exc}")

        # 3. Persist incoming user message
        try:
            await client.post(f"{DB_URL}/messages", json={
                "channel_id": req.channel_id,
                "role":       "user",
                "username":   req.username,
                "content":    req.message,
            })
        except Exception as exc:
            log.warning(f"[db] Could not persist user message: {exc}")

        # 4. Build messages array (same shape for Ollama and OpenAI)
        system_content = SYSTEM_PROMPT + location_context + f"\nToday's date is {date.today()}"
        messages = [{"role": "system", "content": system_content}]
        for h in history:
            prefix = f"{h['username']}: " if h.get("username") else ""
            messages.append({"role": h["role"], "content": prefix + h["content"]})
        messages.append({"role": "user", "content": f"{req.username}: {req.message}"})

        # 5. Call LLM backend
        raw_reply = await (call_openai(client, messages) if USE_OPENAI else call_ollama(client, messages))

        # 6. Parse optional <action> block (with fallback for unreliable models)
        action = None
        match = ACTION_PATTERN.search(raw_reply)
        if match:
            try:
                action = json.loads(match.group(1).strip())
                log.info(f"[action] Parsed from <action> tags: {action}")
            except json.JSONDecodeError as exc:
                log.warning(f"[action] Malformed action JSON in tags: {exc}")
            raw_reply = ACTION_PATTERN.sub("", raw_reply).strip()

        # Fallback: try to extract action even without proper tags
        if action is None:
            action = fallback_parse_action(raw_reply)
            if action:
                log.info(f"[action] Parsed via fallback: {action}")
                # Clean the raw JSON from the reply text
                for p in [
                    re.compile(r'<[Aa]ction>.*?</[Aa]ction>', re.DOTALL),
                    re.compile(r'\[action\].*?\[/action\]', re.DOTALL),
                    re.compile(r'```(?:json)?\s*\{[^{}]*"type"\s*:\s*"create_event"[^{}]*\}\s*```', re.DOTALL),
                    re.compile(r'\{[^{}]*"type"\s*:\s*"create_event"[^{}]*\}', re.DOTALL),
                ]:
                    raw_reply = p.sub("", raw_reply).strip()
                    if action:  # stop after first successful clean
                        break

        # 7. Persist assistant reply
        try:
            await client.post(f"{DB_URL}/messages", json={
                "channel_id": req.channel_id,
                "role":       "assistant",
                "username":   "Eve",
                "content":    raw_reply,
            })
        except Exception as exc:
            log.warning(f"[db] Could not persist assistant reply: {exc}")

        # 8. Save pending event record if needed
        db_event_id = None
        if action and action.get("type") == "create_event":
            try:
                ev_resp = await client.post(f"{DB_URL}/events", json={
                    "guild_id":    req.guild_id,
                    "name":        action.get("name", "Unnamed Event"),
                    "description": action.get("description", ""),
                    "start_time":  action.get("start_time", ""),
                })
                if ev_resp.status_code == 201:
                    db_event_id = ev_resp.json().get("id")
                    log.info(f"[db] Saved pending event id={db_event_id}")
                else:
                    log.warning(f"[db] Event save returned {ev_resp.status_code}: {ev_resp.text}")
            except Exception as exc:
                log.warning(f"[db] Could not save pending event: {exc}")

        return {
            "response":    raw_reply,
            "action":      action,
            "db_event_id": db_event_id,
        }


# -- Health ------------------------------------------------------------------

@app.get("/health")
async def health():
    """
    Returns service status + a live Ollama model list (or OpenAI reachability).
    Polled by the bot on startup.
    """
    backend_ok     = False
    backend_detail = ""
    ollama_models  = []

    async with httpx.AsyncClient(timeout=4.0) as client:
        if USE_OPENAI:
            try:
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                )
                backend_ok     = r.status_code == 200
                backend_detail = f"HTTP {r.status_code}"
            except Exception as exc:
                backend_detail = str(exc)
        else:
            try:
                r = await client.get(f"{OLLAMA_URL}/api/tags")
                backend_ok    = r.status_code == 200
                ollama_models = [m["name"] for m in r.json().get("models", [])]
                backend_detail = f"{len(ollama_models)} model(s) available"
            except Exception as exc:
                backend_detail = str(exc)

    status = "ok" if backend_ok else "degraded"
    log.info(f"[health] status={status} | {backend_detail}")
    return {
        "status":            status,
        "model":             OLLAMA_MODEL,
        "backend":           "openai" if USE_OPENAI else "ollama",
        "ollama":            OLLAMA_URL if not USE_OPENAI else None,
        "backend_reachable": backend_ok,
        "backend_detail":    backend_detail,
        "ollama_models":     ollama_models,   # empty list when using OpenAI
    }