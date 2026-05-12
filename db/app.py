"""
Container 3 — Memory / Database Service
Stores conversation history and planned events in SQLite.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

# configure local or DOCKER path
from pathlib import Path

IS_DOCKER = os.path.exists("/.dockerenv")
if IS_DOCKER:
    DB_PATH = "/data/events.db"
else:
    DB_PATH = "./data/events.db"

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
app = FastAPI(title="Event Planner DB")

# ── DB init ────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs("/data", exist_ok=True)
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id  TEXT    NOT NULL,
            role        TEXT    NOT NULL,   -- 'user' | 'assistant'
            username    TEXT,
            content     TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id         TEXT NOT NULL,
            name             TEXT NOT NULL,
            description      TEXT,
            start_time       TEXT NOT NULL,
            discord_event_id TEXT,          -- filled in after Discord confirms
            created_at       TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    print("DB initialised at", DB_PATH)


init_db()


# ── Schemas ────────────────────────────────────────────────────────────────

class MessageIn(BaseModel):
    channel_id: str
    role: str
    username: Optional[str] = None
    content: str


class EventIn(BaseModel):
    guild_id: str
    name: str
    description: Optional[str] = None
    start_time: str                   # ISO 8601
    discord_event_id: Optional[str] = None


class EventUpdate(BaseModel):
    discord_event_id: str


# ── Message endpoints ──────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service": "event-planner-db",
        "status": "running"
    }

@app.get("/messages/{channel_id}")
def get_messages(channel_id: str, limit: int = 20):
    """Return the most recent `limit` messages for a channel, oldest-first."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM messages WHERE channel_id = ? ORDER BY id DESC LIMIT ?",
        (channel_id, limit),
    ).fetchall()
    conn.close()
    return list(reversed([dict(r) for r in rows]))


@app.post("/messages", status_code=201)
def save_message(msg: MessageIn):
    conn = get_conn()
    conn.execute(
        "INSERT INTO messages (channel_id, role, username, content, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        (msg.channel_id, msg.role, msg.username, msg.content,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ── Event endpoints ────────────────────────────────────────────────────────

@app.get("/events/{guild_id}")
def get_events(guild_id: str):
    """Return all events for a guild, sorted by start time."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE guild_id = ? ORDER BY start_time",
        (guild_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/events", status_code=201)
def save_event(event: EventIn):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO events (guild_id, name, description, start_time, discord_event_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event.guild_id, event.name, event.description, event.start_time,
         event.discord_event_id, datetime.now(timezone.utc).isoformat()),
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"status": "ok", "id": event_id}


@app.patch("/events/{event_id}")
def update_event(event_id: int, body: EventUpdate):
    """Patch in the Discord event ID once the bot has created it."""
    conn = get_conn()
    conn.execute(
        "UPDATE events SET discord_event_id = ? WHERE id = ?",
        (body.discord_event_id, event_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}