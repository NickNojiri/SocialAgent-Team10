"""Admin web UI for the event catalog — add / view / remove / vote.

Runs on the HOST (needs the full ingestion deps: Playwright, LLM, etc.) and
reads/writes the SAME Chroma store the bot + recommend service use, so anything
you add or remove here shows up to the Discord bot immediately.

    uvicorn src.ingestion.serving.admin:app --port 8010
    # then open http://localhost:8010

Votes are stored in each event's Chroma metadata (`votes`), so the recommender
can later rank by popularity.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.ingestion.cli import (
    build_extractor,
    build_temporal_resolver,
)
from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.orchestrator import IngestionPipeline, result_line
from src.ingestion.pipeline.summarizer import summarize_place
from src.ingestion.sinks.chroma_sink import ChromaSink, collection_for_guild
from src.ingestion.sinks.jsonl_sink import JsonlSink

app = FastAPI(title="SocialAgent Admin")

# Speed/quality knobs, tunable from .env without code changes. Capture time is
# dominated by three stages: page render (SETTLE_TIMEOUT_MS), Whisper on CPU
# (WHISPER_MODEL / TRANSCRIBE_ENABLED), and Ollama inference (OLLAMA_MODEL —
# e.g. llama3.2:3b is ~2-3x faster than llama3.1:8b at some quality cost).
_env_overrides: dict = {}
for _env, _field, _cast in (
    ("OLLAMA_URL", "ollama_url", str),
    ("OLLAMA_MODEL", "ollama_model", str),
    ("WHISPER_MODEL", "whisper_model", str),
    ("SETTLE_TIMEOUT_MS", "settle_timeout_ms", int),
    ("CAPTURE_BUDGET_S", "capture_budget_s", float),
):
    _val = os.getenv(_env, "").strip()
    if _val:
        _env_overrides[_field] = _cast(_val)

_settings = IngestionSettings(chroma_enabled=True, geocode_enabled=False, **_env_overrides)
_TRANSCRIBE_OFF = os.getenv("TRANSCRIBE_ENABLED", "").strip().lower() in ("0", "false", "off", "no")

# One catalog per Discord guild (or DM stash). "" is the legacy/single-tenant
# collection, so existing data and the web UI keep working unchanged.
_sinks: dict[str, ChromaSink] = {}


def _sink_for(guild_id: str = "") -> ChromaSink:
    key = str(guild_id or "")
    if key not in _sinks:
        _sinks[key] = ChromaSink(
            _settings, collection_name=collection_for_guild(_settings, key)
        )
    return _sinks[key]

# Route host TLS through the Windows cert store so the reel video download works on
# TLS-intercepting networks (see the transcriber). Best-effort.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    pass


def _build_transcriber():
    """Whisper transcriber for reel audio, built once (model loads on first use).
    Stays None when faster-whisper isn't installed — or when TRANSCRIBE_ENABLED=0
    (the biggest single speed win on CPU-only machines) — so ingest skips audio."""
    if _TRANSCRIBE_OFF:
        import logging

        logging.getLogger("ingestion.admin").info("[stt] transcription disabled via TRANSCRIBE_ENABLED=0")
        return None
    try:
        from faster_whisper import WhisperModel

        from src.ingestion.pipeline.transcriber import Transcriber

        model = WhisperModel(_settings.whisper_model, device="cpu", compute_type="int8")

        def _fn(path: str):
            segments, _info = model.transcribe(path, beam_size=1, vad_filter=True)
            return " ".join(s.text for s in segments).strip()

        return Transcriber(_settings, transcribe_fn=_fn)
    except Exception as exc:
        import logging

        logging.getLogger("ingestion.admin").warning(f"[stt] transcription off: {exc}")
        return None


_transcriber = _build_transcriber()


def _build_ig_source():
    """Authenticated IG fetch source, built once when IG_USERNAME/IG_PASSWORD are
    set (and instagrapi is installed). None → the pipeline uses the Playwright
    path exactly as before. Login is lazy (on first capture)."""
    if not (_settings.ig_username and _settings.ig_password):
        return None
    try:
        from src.ingestion.sources.ig_authed import AuthedInstagramSource

        import logging

        logging.getLogger("ingestion.admin").info(
            "[ig] authenticated fetch source enabled (user=%s)", _settings.ig_username
        )
        return AuthedInstagramSource(_settings)
    except Exception as exc:
        import logging

        logging.getLogger("ingestion.admin").warning(f"[ig] authed source off: {exc}")
        return None


_ig_source = _build_ig_source()


def _build_ocr():
    """Cover-image OCR (on-screen text), opt-in via OCR_ENABLED=1. The reader
    degrades to no-text when rapidocr isn't installed, so this never fails."""
    if os.getenv("OCR_ENABLED", "").strip().lower() not in ("1", "true", "on", "yes"):
        return None
    from src.ingestion.pipeline.ocr import ImageTextReader

    return ImageTextReader(_settings)


_ocr_reader = _build_ocr()

# /api/ingest input guardrails — reject garbage instantly instead of feeding it
# a 30s browser cycle.
_MAX_URLS_PER_INGEST = 10


class IngestBody(BaseModel):
    urls: list[str]
    guild_id: str = ""      # "" → the legacy/single-tenant catalog


class VoteBody(BaseModel):
    delta: int = 1
    # When set, votes carry identity: "Want to go" joins the voters list,
    # "Not for me" leaves it, and the count is the list's size. Anonymous
    # votes (the web UI) keep the plain counter behavior.
    user_id: str = ""
    user_name: str = ""


def _voters(meta: dict) -> dict:
    """The {user_id: display_name} map stored as a JSON string in metadata."""
    try:
        return json.loads(meta.get("voters") or "{}")
    except (TypeError, ValueError):
        return {}


def _apply_vote(meta: dict, body: VoteBody) -> dict:
    """Pure vote transition — split out so the quorum logic is testable offline."""
    if body.user_id:
        voters = _voters(meta)
        if body.delta > 0:
            voters[body.user_id] = body.user_name or body.user_id
        else:
            voters.pop(body.user_id, None)
        meta["voters"] = json.dumps(voters)
        meta["votes"] = len(voters)
    else:
        meta["votes"] = int(meta.get("votes", 0)) + body.delta
    return meta


# ── API ──────────────────────────────────────────────────────────────────────


@app.get("/api/events")
def list_events(guild_id: str = ""):
    res = _sink_for(guild_id).collection.get(include=["metadatas"])
    events = []
    for event_id, m in zip(res["ids"], res["metadatas"]):
        m = m or {}
        events.append(
            {
                "id": event_id,
                "venue": m.get("venue_name", "Unknown"),
                "category": m.get("category", "other"),
                "theme": m.get("core_theme", ""),
                "source_url": m.get("source_url", ""),
                "image": m.get("image_url", ""),
                "lat": m.get("lat"),
                "lng": m.get("lng"),
                "blurb": m.get("summary", ""),
                "votes": int(m.get("votes", 0)),
                "voters": sorted(_voters(m).values()),
                "schedule": m.get("schedule_status", "unscheduled"),
                "start_utc": m.get("start_utc", ""),
            }
        )
    events.sort(key=lambda e: (-e["votes"], e["venue"]))
    return {"events": events, "count": len(events)}


@app.post("/api/ingest")
async def ingest(body: IngestBody):
    urls = [u.strip() for u in body.urls if u.strip()]
    if not urls:
        raise HTTPException(400, "no urls given")
    if len(urls) > _MAX_URLS_PER_INGEST:
        raise HTTPException(400, f"too many links — max {_MAX_URLS_PER_INGEST} per request")
    bad = [u for u in urls if not u.lower().startswith(("http://", "https://"))]
    if bad:
        raise HTTPException(400, f"only http(s) URLs are ingestible, got: {bad[0][:80]!r}")
    sink = _sink_for(body.guild_id)
    existing_ids = set(sink.collection.get(include=[])["ids"])  # snapshot before the run
    pipeline = IngestionPipeline(
        _settings,
        extractor=build_extractor(_settings),
        transcriber=_transcriber,
        summarizer=lambda cap, tr: summarize_place(cap, tr, _settings),
        geo_enricher=None,  # geocoding off here (fast; Nominatim often blocked)
        temporal_resolver=build_temporal_resolver(_settings),
        jsonl_sink=JsonlSink(Path("data/inspirations.jsonl")),
        chroma_sink=sink,
        authed_source=_ig_source,
        ocr_reader=_ocr_reader,
    )
    report = await pipeline.run(urls)
    return {
        "added": len(report.validated),
        "rejected": len(report.rejected),
        "unreadable": len(report.connectivity_failures),
        # Per-event detail the Discord bot needs to build cards + vote buttons.
        "events": [_event_summary(r, existing_ids, sink) for r in report.validated],
        "log": [result_line(r) for r in report.results],
    }


def _event_summary(result, existing_ids: set, sink: ChromaSink) -> dict:
    """Shape one validated record for the bot: id + display fields + live vote count.

    `new` distinguishes a first-time add from re-sharing an already-cataloged reel
    (the Chroma id is the content hash, so re-ingest upserts rather than duplicates).
    """
    cid = result.record.provenance.content_hash
    got = sink.collection.get(ids=[cid], include=["metadatas"])
    meta = (got["metadatas"][0] if got["ids"] else {}) or {}
    return {
        "id": cid,
        "venue": meta.get("venue_name") or result.record.venue_name,
        "category": meta.get("category") or result.record.category.value,
        "theme": meta.get("core_theme") or result.record.core_theme,
        "source_url": meta.get("source_url", ""),
        "image": meta.get("image_url", ""),
        "lat": meta.get("lat"),
        "lng": meta.get("lng"),
        "blurb": meta.get("summary", ""),          # video-based quick description ("" → "No info")
        "schedule": meta.get("schedule_status", "unscheduled"),
        "start_epoch": meta.get("start_epoch"),
        "end_epoch": meta.get("end_epoch"),
        "votes": int(meta.get("votes", 0)),
        "voters": sorted(_voters(meta).values()),
        "new": cid not in existing_ids,
    }


class ManualBody(BaseModel):
    venue: str
    theme: str
    source_url: str = ""
    guild_id: str = ""


def _manual_record(venue: str, theme: str, source_url: str = ""):
    """A validated EventInspiration from user-typed fields (the Discord modal).
    Pure — no sink, no network — so the mapping is testable offline."""
    from datetime import datetime, timezone

    from src.ingestion.pipeline.normalizer import _categorize
    from src.ingestion.schemas.inspiration import (
        EventInspiration, GeoContext, SourceProvenance, content_hash,
    )

    venue = venue.strip()[:110]
    theme = theme.strip()[:280]
    return EventInspiration(
        venue_name=venue,
        core_theme=theme,
        category=_categorize(f"{venue} {theme}".lower()),
        geo=GeoContext(),
        provenance=SourceProvenance(
            source_url=source_url.strip() or "manual://discord",
            platform="generic",
            fetched_at=datetime.now(timezone.utc),
            content_hash=content_hash(f"manual:{venue}:{theme}"),
            extractor="manual/1.0",
        ),
    )


@app.post("/api/manual")
def add_manual(body: ManualBody):
    """Manual catalog entry — the never-waste-a-paste fallback for failed captures."""
    if not body.venue.strip() or len(body.theme.strip()) < 3:
        raise HTTPException(400, "venue and a short vibe description are required")
    try:
        record = _manual_record(body.venue, body.theme, body.source_url)
    except Exception as exc:
        raise HTTPException(400, f"couldn't build a valid spot from that: {exc}")
    sink = _sink_for(body.guild_id)
    event_id = sink.add(record)
    src = str(record.provenance.source_url)
    return {
        "id": event_id,
        "venue": record.venue_name,
        "category": record.category.value,
        "theme": record.core_theme,
        "source_url": "" if src.startswith("manual:") else src,
        "votes": 0,
        "new": True,
    }


@app.delete("/api/events/{event_id}")
def delete_event(event_id: str, guild_id: str = ""):
    _sink_for(guild_id).collection.delete(ids=[event_id])
    return {"deleted": event_id}


@app.post("/api/events/{event_id}/vote")
def vote(event_id: str, body: VoteBody, guild_id: str = ""):
    sink = _sink_for(guild_id)
    res = sink.collection.get(ids=[event_id], include=["metadatas"])
    if not res["ids"]:
        raise HTTPException(404, "event not found")
    meta = _apply_vote(res["metadatas"][0] or {}, body)
    sink.collection.update(ids=[event_id], metadatas=[meta])
    return {
        "id": event_id,
        "votes": meta["votes"],
        "voters": sorted(_voters(meta).values()),
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


@app.get("/share", response_class=HTMLResponse)
def share():
    """Read-only catalog page — safe to send to people outside the server.

    The growth surface (docs/PRODUCT_ROADMAP.md 2.3): viewable without the bot
    installed, no add/vote/remove controls, with an install call-to-action.
    Guild selection via ?guild_id=… (default: the single-tenant catalog).
    """
    return _SHARE_PAGE


# ── UI ───────────────────────────────────────────────────────────────────────

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SocialAgent — Event Catalog</title>
<style>
  :root{--bg:#0f1117;--card:#1a1d27;--line:#2a2e3a;--txt:#e7e9ee;--mut:#9aa0ad;--acc:#6ea8fe;--ok:#3fb950;--bad:#f85149}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,Segoe UI,sans-serif}
  header{padding:20px 24px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:20px} .sub{color:var(--mut);font-size:13px;margin-top:4px}
  main{max-width:860px;margin:0 auto;padding:24px}
  .add{display:flex;gap:8px;margin-bottom:8px}
  textarea{flex:1;background:var(--card);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:10px;font:inherit;min-height:44px;resize:vertical}
  button{background:var(--acc);color:#06121f;border:0;border-radius:8px;padding:0 16px;font-weight:600;cursor:pointer}
  button:disabled{opacity:.5;cursor:wait}
  .hint{color:var(--mut);font-size:12px;margin-bottom:18px}
  #status{min-height:20px;font-size:13px;margin:8px 0;white-space:pre-wrap}
  .count{color:var(--mut);font-size:13px;margin:16px 0 8px}
  .ev{display:flex;gap:14px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:10px}
  .votes{display:flex;flex-direction:column;align-items:center;min-width:46px}
  .votes b{font-size:18px} .vb{background:none;border:1px solid var(--line);color:var(--mut);border-radius:6px;width:34px;padding:2px 0;font-size:14px;margin:1px 0}
  .vb:hover{color:var(--txt);border-color:var(--acc)}
  .meta{flex:1;min-width:0}
  .venue{font-weight:700;font-size:16px}
  .badge{display:inline-block;background:#26304a;color:var(--acc);border-radius:999px;padding:1px 9px;font-size:11px;margin-left:8px;vertical-align:middle}
  .theme{color:var(--mut);font-size:13px;margin:4px 0;overflow:hidden;text-overflow:ellipsis}
  .row{font-size:12px;color:var(--mut)} .row a{color:var(--acc);text-decoration:none}
  .del{background:none;border:1px solid var(--line);color:var(--bad);border-radius:6px;padding:6px 10px;cursor:pointer;align-self:center}
  .del:hover{border-color:var(--bad)}
  .empty{color:var(--mut);text-align:center;padding:40px}
</style></head><body>
<header><h1>🎟️ SocialAgent — Event Catalog</h1>
<div class="sub">Add public post URLs, curate the list, and vote. Changes are live to the Discord bot.</div></header>
<main>
  <div class="add">
    <textarea id="urls" placeholder="Paste one or more public post URLs (one per line)…"></textarea>
    <button id="addBtn" onclick="addUrls()">Add</button>
  </div>
  <div class="hint">Ingesting runs the full pipeline (fetch → LLM → schedule) — ~20–30s per URL. Login-walled or expired posts are skipped.</div>
  <div id="status"></div>
  <div class="count" id="count"></div>
  <div id="list"></div>
</main>
<script>
const EMOJI={food_drink:"🍽️",cafe_dessert:"🍰",nightlife:"🍸",live_music:"🎶",market_popup:"🛍️",outdoors:"🏞️",community:"🤝",other:"📍"};
async function load(){
  const r=await fetch('/api/events'); const d=await r.json();
  document.getElementById('count').textContent=d.count+' event'+(d.count===1?'':'s')+' in the catalog';
  const list=document.getElementById('list');
  if(!d.events.length){list.innerHTML='<div class="empty">No events yet — add a URL above.</div>';return;}
  list.innerHTML=d.events.map(e=>`
    <div class="ev">
      <div class="votes">
        <button class="vb" onclick="vote('${e.id}',1)">▲</button>
        <b>${e.votes}</b>
        <button class="vb" onclick="vote('${e.id}',-1)">▼</button>
      </div>
      <div class="meta">
        <div><span class="venue">${esc(e.venue)}</span><span class="badge">${EMOJI[e.category]||'📍'} ${esc(e.category.replace(/_/g,' '))}</span></div>
        <div class="theme">${esc(e.theme||'')}</div>
        <div class="row">${e.schedule==='scheduled'&&e.start_utc?('🗓️ '+new Date(e.start_utc).toLocaleString()+' · '):''}${e.source_url?`<a href="${esc(e.source_url)}" target="_blank">source ↗</a>`:''}</div>
      </div>
      <button class="del" onclick="del('${e.id}')">🗑 Remove</button>
    </div>`).join('');
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function addUrls(){
  const ta=document.getElementById('urls'); const btn=document.getElementById('addBtn'); const st=document.getElementById('status');
  const urls=ta.value.split('\\n').map(s=>s.trim()).filter(Boolean);
  if(!urls.length){st.textContent='Paste at least one URL.';return;}
  btn.disabled=true; st.textContent='⏳ Ingesting '+urls.length+' URL(s)… (~20–30s each)';
  try{
    const r=await fetch('/api/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({urls})});
    const d=await r.json();
    st.textContent=`✅ added ${d.added} · ⚠️ rejected ${d.rejected} · 🚫 unreadable ${d.unreadable}\\n`+(d.log||[]).join('\\n');
    ta.value='';
  }catch(e){st.textContent='❌ '+e;}
  btn.disabled=false; load();
}
async function vote(id,delta){await fetch('/api/events/'+id+'/vote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delta})});load();}
async function del(id){if(!confirm('Remove this event?'))return;await fetch('/api/events/'+id,{method:'DELETE'});load();}
load();
</script>
</body></html>"""


_SHARE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>SpotBot — our spots</title>
<style>
  :root{--bg:#0f1117;--card:#1a1d27;--line:#2a2e3a;--txt:#e7e9ee;--mut:#9aa0ad;--acc:#6ea8fe;--ok:#3fb950}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,Segoe UI,sans-serif}
  header{padding:20px 24px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:20px} .sub{color:var(--mut);font-size:13px;margin-top:4px}
  main{max-width:860px;margin:0 auto;padding:24px}
  .count{color:var(--mut);font-size:13px;margin:0 0 12px}
  .ev{display:flex;gap:14px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:10px}
  .thumb{width:72px;height:72px;border-radius:8px;object-fit:cover;background:#26304a;flex:none}
  .votes{min-width:56px;text-align:center;color:var(--ok);font-weight:700;font-size:16px;align-self:center}
  .votes span{display:block;color:var(--mut);font-weight:400;font-size:11px}
  .meta{flex:1;min-width:0}
  .venue{font-weight:700;font-size:16px}
  .badge{display:inline-block;background:#26304a;color:var(--acc);border-radius:999px;padding:1px 9px;font-size:11px;margin-left:8px;vertical-align:middle}
  .theme{color:var(--mut);font-size:13px;margin:4px 0;overflow:hidden;text-overflow:ellipsis}
  .row{font-size:12px;color:var(--mut)} .row a{color:var(--acc);text-decoration:none}
  .empty{color:var(--mut);text-align:center;padding:40px}
  footer{max-width:860px;margin:0 auto;padding:8px 24px 32px;color:var(--mut);font-size:13px}
  footer a{color:var(--acc);text-decoration:none;font-weight:600}
</style></head><body>
<header><h1>📍 Our spots</h1>
<div class="sub">A group catalog of places we want to go — captured from shared reels.</div></header>
<main>
  <div class="count" id="count"></div>
  <div id="list"></div>
</main>
<footer>Powered by <a href="https://github.com/NickNojiri/SocialAgent-Team10" target="_blank">SpotBot</a>
— paste a reel in Discord, get a votable spot. Add it to your server.</footer>
<script>
const EMOJI={food_drink:"🍽️",cafe_dessert:"🍰",nightlife:"🍸",live_music:"🎶",market_popup:"🛍️",outdoors:"🏞️",community:"🤝",other:"📍"};
const GUILD=new URLSearchParams(location.search).get('guild_id')||'';
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function load(){
  const r=await fetch('/api/events?guild_id='+encodeURIComponent(GUILD)); const d=await r.json();
  document.getElementById('count').textContent=d.count+' spot'+(d.count===1?'':'s');
  const list=document.getElementById('list');
  if(!d.events.length){list.innerHTML='<div class="empty">Nothing here yet.</div>';return;}
  list.innerHTML=d.events.map(e=>`
    <div class="ev">
      ${e.image?`<img class="thumb" src="${esc(e.image)}" alt=""/>`:''}
      <div class="meta">
        <div><span class="venue">${esc(e.venue)}</span><span class="badge">${EMOJI[e.category]||'📍'} ${esc((e.category||'other').replace(/_/g,' '))}</span></div>
        <div class="theme">${esc(e.theme||'')}</div>
        <div class="row">
          ${e.schedule==='scheduled'&&e.start_utc?('🗓️ '+new Date(e.start_utc).toLocaleString()+' · '):''}
          ${(e.lat!=null&&e.lng!=null)?`<a href="https://www.openstreetmap.org/?mlat=${e.lat}&mlon=${e.lng}#map=17/${e.lat}/${e.lng}" target="_blank">map ↗</a> · `:''}
          ${e.source_url?`<a href="${esc(e.source_url)}" target="_blank">source ↗</a>`:''}
        </div>
      </div>
      <div class="votes">👍 ${e.votes}<span>${esc((e.voters||[]).slice(0,4).join(', '))}</span></div>
    </div>`).join('');
}
load();
</script>
</body></html>"""
