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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
app = FastAPI(title="Event Planner DB")

# ── DB init ────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
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

        CREATE TABLE IF NOT EXISTS user_locations (
            user_id      TEXT NOT NULL,
            guild_id     TEXT NOT NULL,
            display_name TEXT,
            location_text TEXT NOT NULL,    -- e.g. "Long Beach, CA"
            lat          REAL,             -- optional GPS coords
            lng          REAL,             -- optional GPS coords
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (user_id, guild_id)
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


class LocationIn(BaseModel):
    user_id: str
    guild_id: str
    display_name: Optional[str] = None
    location_text: str
    lat: Optional[float] = None
    lng: Optional[float] = None


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


# ── Location endpoints ─────────────────────────────────────────────────────

@app.post("/locations", status_code=201)
def save_location(loc: LocationIn):
    """Save or update a user's location (upsert by user_id + guild_id)."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO user_locations (user_id, guild_id, display_name, location_text, lat, lng, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, guild_id) DO UPDATE SET "
        "display_name = excluded.display_name, "
        "location_text = excluded.location_text, "
        "lat = excluded.lat, "
        "lng = excluded.lng, "
        "updated_at = excluded.updated_at",
        (loc.user_id, loc.guild_id, loc.display_name, loc.location_text,
         loc.lat, loc.lng, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/locations/{guild_id}")
def get_locations(guild_id: str):
    """Return all saved member locations for a guild."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM user_locations WHERE guild_id = ? ORDER BY display_name",
        (guild_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/locater", response_class=HTMLResponse)
def serve_locater_page(user_id: str, guild_id: str, display_name: str):
    """Serves a lightweight HTML page that asks for browser GPS."""
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>SocialAgent - Share Location</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; text-align: center; padding: 50px 20px; background-color: #36393f; color: white; }}
            .btn {{ background-color: #5865F2; color: white; border: none; padding: 15px 32px; text-align: center; text-decoration: none; display: inline-block; font-size: 16px; margin: 4px 2px; cursor: pointer; border-radius: 8px; font-weight: bold; }}
            .btn:hover {{ background-color: #4752C4; }}
            #status {{ margin-top: 20px; font-size: 14px; color: #b9bbbe; }}
        </style>
    </head>
    <body>
        <h2>Hello, {display_name}! 👋</h2>
        <p>Eve needs your location to plan fair meeting spots.</p>
        <button class="btn" onclick="getLocation()">📍 Share My Live Location</button>
        <p id="status"></p>

        <script>
            const statusText = document.getElementById("status");

            function getLocation() {{
                if (navigator.geolocation) {{
                    statusText.innerHTML = "Locating...";
                    navigator.geolocation.getCurrentPosition(sendPosition, showError);
                }} else {{
                    statusText.innerHTML = "Geolocation is not supported by this browser.";
                }}
            }}

            async function sendPosition(position) {{
                const lat = position.coords.latitude;
                const lng = position.coords.longitude;
                statusText.innerHTML = "Saving to database...";
                
                try {{
                    const response = await fetch('/locations', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{
                            user_id: "{user_id}",
                            guild_id: "{guild_id}",
                            display_name: "{display_name}",
                            location_text: "Live GPS Location",
                            lat: lat,
                            lng: lng
                        }})
                    }});
                    
                    if(response.ok) {{
                        statusText.innerHTML = "✅ Success! You can close this page and return to Discord.";
                        statusText.style.color = "#43b581";
                    }} else {{
                        statusText.innerHTML = "❌ Failed to save location.";
                        statusText.style.color = "#f04747";
                    }}
                }} catch(err) {{
                    statusText.innerHTML = "❌ Network error.";
                }}
            }}

            function showError(error) {{
                switch(error.code) {{
                    case error.PERMISSION_DENIED: statusText.innerHTML = "User denied the request for Geolocation."; break;
                    case error.POSITION_UNAVAILABLE: statusText.innerHTML = "Location information is unavailable."; break;
                    case error.TIMEOUT: statusText.innerHTML = "The request to get user location timed out."; break;
                    case error.UNKNOWN_ERROR: statusText.innerHTML = "An unknown error occurred."; break;
                }}
                statusText.style.color = "#f04747";
            }}
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)