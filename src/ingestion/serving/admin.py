"""Admin web UI for the event catalog — add / view / remove / vote.

Runs on the HOST (needs the full ingestion deps: Playwright, LLM, etc.) and
reads/writes the SAME Chroma store the bot + recommend service use, so anything
you add or remove here shows up to the Discord bot immediately.

    uvicorn src.ingestion.serving.admin:app --port 8010
    # then open http://localhost:8010

Votes are stored in each event's Chroma metadata (`votes`), so the recommender
can later rank by popularity.
"""

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
from src.ingestion.sinks.chroma_sink import ChromaSink
from src.ingestion.sinks.jsonl_sink import JsonlSink

app = FastAPI(title="SocialAgent Admin")
_settings = IngestionSettings(chroma_enabled=True, geocode_enabled=False)
_sink = ChromaSink(_settings)


class IngestBody(BaseModel):
    urls: list[str]


class VoteBody(BaseModel):
    delta: int = 1


# ── API ──────────────────────────────────────────────────────────────────────


@app.get("/api/events")
def list_events():
    res = _sink.collection.get(include=["metadatas"])
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
                "votes": int(m.get("votes", 0)),
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
    existing_ids = set(_sink.collection.get(include=[])["ids"])  # snapshot before the run
    pipeline = IngestionPipeline(
        _settings,
        extractor=build_extractor(_settings),
        geo_enricher=None,  # geocoding off here (fast; Nominatim often blocked)
        temporal_resolver=build_temporal_resolver(_settings),
        jsonl_sink=JsonlSink(Path("data/inspirations.jsonl")),
        chroma_sink=_sink,
    )
    report = await pipeline.run(urls)
    return {
        "added": len(report.validated),
        "rejected": len(report.rejected),
        "unreadable": len(report.connectivity_failures),
        # Per-event detail the Discord bot needs to build cards + vote buttons.
        "events": [_event_summary(r, existing_ids) for r in report.validated],
        "log": [result_line(r) for r in report.results],
    }


def _event_summary(result, existing_ids: set) -> dict:
    """Shape one validated record for the bot: id + display fields + live vote count.

    `new` distinguishes a first-time add from re-sharing an already-cataloged reel
    (the Chroma id is the content hash, so re-ingest upserts rather than duplicates).
    """
    cid = result.record.provenance.content_hash
    got = _sink.collection.get(ids=[cid], include=["metadatas"])
    meta = (got["metadatas"][0] if got["ids"] else {}) or {}
    return {
        "id": cid,
        "venue": meta.get("venue_name") or result.record.venue_name,
        "category": meta.get("category") or result.record.category.value,
        "theme": meta.get("core_theme") or result.record.core_theme,
        "source_url": meta.get("source_url", ""),
        "schedule": meta.get("schedule_status", "unscheduled"),
        "start_epoch": meta.get("start_epoch"),
        "end_epoch": meta.get("end_epoch"),
        "votes": int(meta.get("votes", 0)),
        "new": cid not in existing_ids,
    }


@app.delete("/api/events/{event_id}")
def delete_event(event_id: str):
    _sink.collection.delete(ids=[event_id])
    return {"deleted": event_id}


@app.post("/api/events/{event_id}/vote")
def vote(event_id: str, body: VoteBody):
    res = _sink.collection.get(ids=[event_id], include=["metadatas"])
    if not res["ids"]:
        raise HTTPException(404, "event not found")
    meta = res["metadatas"][0] or {}
    meta["votes"] = int(meta.get("votes", 0)) + body.delta
    _sink.collection.update(ids=[event_id], metadatas=[meta])
    return {"id": event_id, "votes": meta["votes"]}


@app.get("/", response_class=HTMLResponse)
def index():
    return _PAGE


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
