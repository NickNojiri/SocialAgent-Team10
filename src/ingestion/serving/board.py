"""Team suggestion board — track what to build next.

Endpoints (all prefixed /board):
  GET  /           serve the HTML UI
  GET  /suggestions           list all, sorted by votes desc
  POST /suggestions           add a new suggestion
  POST /suggestions/{id}/vote upvote by 1
  PATCH /suggestions/{id}     update status (backlog / in_progress / done)
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter(prefix="/board")

Status = Literal["backlog", "in_progress", "done"]
Category = Literal["feature", "improvement", "bug", "idea"]

_DB_PATH = Path("data/board.sqlite3")

_CREATE = """
CREATE TABLE IF NOT EXISTS suggestions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    category    TEXT DEFAULT 'idea',
    status      TEXT DEFAULT 'backlog',
    votes       INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL
)
"""


def _db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE)
    conn.commit()
    return conn


class SuggestionIn(BaseModel):
    title: str
    description: Optional[str] = None
    category: Category = "idea"


class StatusUpdate(BaseModel):
    status: Status


@router.post("/suggestions", status_code=201)
def add_suggestion(body: SuggestionIn):
    row_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            "INSERT INTO suggestions (id, title, description, category, status, votes, created_at) VALUES (?,?,?,?,?,?,?)",
            (row_id, body.title, body.description, body.category, "backlog", 0, now),
        )
    return {"id": row_id}


@router.get("/suggestions")
def list_suggestions(status: Optional[str] = None):
    with _db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM suggestions WHERE status=? ORDER BY votes DESC, created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM suggestions ORDER BY votes DESC, created_at DESC"
            ).fetchall()
    return [dict(r) for r in rows]


@router.post("/suggestions/{row_id}/vote")
def vote(row_id: str):
    with _db() as conn:
        cur = conn.execute("UPDATE suggestions SET votes=votes+1 WHERE id=?", (row_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "suggestion not found")
        votes = conn.execute(
            "SELECT votes FROM suggestions WHERE id=?", (row_id,)
        ).fetchone()["votes"]
    return {"votes": votes}


@router.patch("/suggestions/{row_id}")
def update_status(row_id: str, body: StatusUpdate):
    with _db() as conn:
        cur = conn.execute("UPDATE suggestions SET status=? WHERE id=?", (body.status, row_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "suggestion not found")
    return {"ok": True}


_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SocialAgent · Suggestion Board</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f0f11; color: #e5e5e8; min-height: 100vh; }
  header { padding: 1.5rem 2rem; border-bottom: 1px solid #222; display: flex; align-items: center; gap: 1rem; }
  header h1 { font-size: 1.25rem; font-weight: 600; }
  header span { font-size: 0.8rem; color: #555; }
  .toolbar { padding: 1rem 2rem; display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
  .filter-btn { background: #1a1a1f; border: 1px solid #2a2a30; color: #888; padding: 0.35rem 0.9rem; border-radius: 999px; cursor: pointer; font-size: 0.8rem; transition: all .15s; }
  .filter-btn.active, .filter-btn:hover { background: #5865f2; border-color: #5865f2; color: #fff; }
  .add-btn { margin-left: auto; background: #5865f2; border: none; color: #fff; padding: 0.45rem 1.1rem; border-radius: 7px; cursor: pointer; font-size: 0.85rem; font-weight: 500; }
  .add-btn:hover { background: #4752c4; }
  .board { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 1rem; padding: 0 2rem 3rem; }
  .card { background: #16161a; border: 1px solid #2a2a30; border-radius: 10px; padding: 1rem 1rem 0.75rem; display: flex; flex-direction: column; gap: 0.55rem; }
  .card h3 { font-size: 0.93rem; font-weight: 600; line-height: 1.35; }
  .card p { font-size: 0.81rem; color: #777; line-height: 1.55; }
  .badges { display: flex; gap: 0.4rem; flex-wrap: wrap; }
  .badge { font-size: 0.69rem; padding: 0.2rem 0.55rem; border-radius: 999px; font-weight: 600; letter-spacing: .02em; }
  .badge-feature     { background: #1e3a5f; color: #58a6ff; }
  .badge-improvement { background: #1e3a2f; color: #56d364; }
  .badge-bug         { background: #3a1e1e; color: #ff7b72; }
  .badge-idea        { background: #2e1e3a; color: #c084fc; }
  .badge-backlog     { background: #222; color: #666; }
  .badge-in_progress { background: #2e2410; color: #e3a008; }
  .badge-done        { background: #1a3028; color: #56d364; }
  .card-actions { display: flex; gap: 0.5rem; align-items: center; margin-top: 0.25rem; }
  .vote-btn { background: #1a1a1f; border: 1px solid #2a2a30; color: #888; padding: 0.28rem 0.65rem; border-radius: 6px; cursor: pointer; font-size: 0.8rem; display: flex; align-items: center; gap: 0.3rem; transition: all .15s; }
  .vote-btn:hover { border-color: #5865f2; color: #5865f2; }
  select.status-sel { background: #1a1a1f; border: 1px solid #2a2a30; color: #888; padding: 0.3rem 0.5rem; border-radius: 6px; font-size: 0.78rem; cursor: pointer; margin-left: auto; }
  .overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.75); z-index: 10; align-items: center; justify-content: center; }
  .overlay.open { display: flex; }
  .modal { background: #16161a; border: 1px solid #2a2a30; border-radius: 12px; padding: 1.5rem; width: 100%; max-width: 460px; display: flex; flex-direction: column; gap: 0.85rem; }
  .modal h2 { font-size: 1rem; font-weight: 600; }
  .modal input, .modal textarea, .modal select { width: 100%; background: #0f0f11; border: 1px solid #2a2a30; color: #e5e5e8; padding: 0.6rem 0.8rem; border-radius: 7px; font-size: 0.85rem; font-family: inherit; outline: none; }
  .modal input:focus, .modal textarea:focus { border-color: #5865f2; }
  .modal textarea { resize: vertical; min-height: 80px; }
  .modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 0.25rem; }
  .cancel-btn { background: none; border: 1px solid #2a2a30; color: #888; padding: 0.4rem 1rem; border-radius: 7px; cursor: pointer; }
  .submit-btn { background: #5865f2; border: none; color: #fff; padding: 0.4rem 1.1rem; border-radius: 7px; cursor: pointer; font-weight: 500; }
  .submit-btn:hover { background: #4752c4; }
  .empty { text-align: center; color: #444; padding: 5rem 2rem; grid-column: 1/-1; font-size: 0.9rem; }
</style>
</head>
<body>
<header>
  <h1>Suggestion Board</h1>
  <span>SocialAgent · what to build next</span>
</header>
<div class="toolbar">
  <button class="filter-btn active" data-status="">All</button>
  <button class="filter-btn" data-status="backlog">Backlog</button>
  <button class="filter-btn" data-status="in_progress">In Progress</button>
  <button class="filter-btn" data-status="done">Done</button>
  <button class="add-btn" onclick="openModal()">+ Add suggestion</button>
</div>
<div class="board" id="board"></div>

<div class="overlay" id="overlay">
  <div class="modal">
    <h2>New Suggestion</h2>
    <input id="m-title" placeholder="Title" maxlength="120" />
    <textarea id="m-desc" placeholder="What and why? (optional)"></textarea>
    <select id="m-cat">
      <option value="idea">Idea</option>
      <option value="feature">Feature</option>
      <option value="improvement">Improvement</option>
      <option value="bug">Bug</option>
    </select>
    <div class="modal-actions">
      <button class="cancel-btn" onclick="closeModal()">Cancel</button>
      <button class="submit-btn" onclick="submit()">Add</button>
    </div>
  </div>
</div>

<script>
let currentFilter = '';

async function load() {
  const url = currentFilter ? `/board/suggestions?status=${currentFilter}` : '/board/suggestions';
  const items = await fetch(url).then(r => r.json());
  const board = document.getElementById('board');
  if (!items.length) {
    board.innerHTML = '<div class="empty">No suggestions yet — add the first one.</div>';
    return;
  }
  board.innerHTML = items.map(s => `
    <div class="card" id="card-${s.id}">
      <div class="badges">
        <span class="badge badge-${s.category}">${s.category}</span>
        <span class="badge badge-${s.status}">${s.status.replace('_',' ')}</span>
      </div>
      <h3>${esc(s.title)}</h3>
      ${s.description ? `<p>${esc(s.description)}</p>` : ''}
      <div class="card-actions">
        <button class="vote-btn" onclick="vote('${s.id}',this)">▲ <span>${s.votes}</span></button>
        <select class="status-sel" onchange="setStatus('${s.id}',this.value)">
          <option value="backlog"     ${s.status==='backlog'     ?'selected':''}>Backlog</option>
          <option value="in_progress" ${s.status==='in_progress' ?'selected':''}>In Progress</option>
          <option value="done"        ${s.status==='done'        ?'selected':''}>Done</option>
        </select>
      </div>
    </div>`).join('');
}

async function vote(id, btn) {
  const data = await fetch(`/board/suggestions/${id}/vote`, {method:'POST'}).then(r => r.json());
  btn.querySelector('span').textContent = data.votes;
}

async function setStatus(id, status) {
  await fetch(`/board/suggestions/${id}`, {
    method: 'PATCH',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({status}),
  });
  load();
}

function openModal()  { document.getElementById('overlay').classList.add('open'); document.getElementById('m-title').focus(); }
function closeModal() { document.getElementById('overlay').classList.remove('open'); }

async function submit() {
  const title = document.getElementById('m-title').value.trim();
  if (!title) { document.getElementById('m-title').focus(); return; }
  await fetch('/board/suggestions', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      title,
      description: document.getElementById('m-desc').value.trim() || null,
      category: document.getElementById('m-cat').value,
    }),
  });
  closeModal();
  document.getElementById('m-title').value = '';
  document.getElementById('m-desc').value = '';
  load();
}

document.querySelectorAll('.filter-btn').forEach(btn =>
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentFilter = btn.dataset.status;
    load();
  })
);

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.getElementById('overlay').addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

load();
</script>
</body>
</html>"""


@router.get("/", response_class=HTMLResponse)
def board_ui():
    return _HTML
