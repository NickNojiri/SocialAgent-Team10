"""
Container 2 — LLM Service
- Pulls conversation history from the DB service.
- Builds a prompt and calls your local Ollama instance.
- Parses any structured actions the model returns (e.g. create_event).
- Persists the assistant reply back to the DB service.
"""

import json
import os
import re

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Event Planner LLM")

OLLAMA_URL = os.getenv("OLLAMA_URL",  "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:latest ")
DB_URL = os.getenv("DB_URL",      "http://db:8002")

# ── System prompt ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Planner — a friendly event coordination assistant living inside a Discord server for a group of friends.

Your job:
• Help the group brainstorm, schedule, and keep track of events (game nights, outings, trips, etc.).
• Remember previous conversations in the channel so you can reference them naturally.
• Be concise and conversational — you're chatting with friends, not writing a report.

Creating events:
When a user asks you to schedule or create an event, respond naturally AND append a machine-readable action block at the very end of your reply (after your text, on its own line):

<action>{"type": "create_event", "name": "...", "description": "...", "start_time": "YYYY-MM-DDTHH:MM:SS"}</action>

Rules for the action block:
- start_time must be a valid ISO 8601 datetime (no timezone suffix needed).
- If the user doesn't mention a specific time, pick something reasonable and tell them what you chose.
- Keep "name" short (Discord event title limit is 100 chars).
- Only emit the block when actually creating an event — not for general questions.
"""

ACTION_PATTERN = re.compile(r"<action>(.*?)</action>", re.DOTALL)


# ── Schema ─────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    channel_id: str
    guild_id:   str
    user_id:    str
    username:   str
    message:    str


# ── Main chat endpoint ─────────────────────────────────────────────────────

@app.post("/chat")
async def chat(req: ChatRequest):
    async with httpx.AsyncClient(timeout=90.0) as client:

        # 1. Fetch conversation history from DB
        hist_resp = await client.get(f"{DB_URL}/messages/{req.channel_id}?limit=20")
        history = hist_resp.json() if hist_resp.status_code == 200 else []

        # 2. Persist the incoming user message
        await client.post(f"{DB_URL}/messages", json={
            "channel_id": req.channel_id,
            "role":       "user",
            "username":   req.username,
            "content":    req.message,
        })

        # 3. Build the Ollama messages array
        ollama_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for h in history:
            # Prefix stored messages with username so the model has social context
            prefix = f"{h['username']}: " if h.get("username") else ""
            ollama_messages.append({"role": h["role"], "content": prefix + h["content"]})
        # Append the current turn
        ollama_messages.append({"role": "user", "content": f"{req.username}: {req.message}"})

        # 4. Call Ollama
        try:
            ollama_resp = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={"model": OLLAMA_MODEL, "messages": ollama_messages, "stream": False},
            )
            ollama_resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Ollama error: {exc}")

        raw_reply: str = ollama_resp.json()["message"]["content"]

        # 5. Parse optional <action> block
        action = None
        match = ACTION_PATTERN.search(raw_reply)
        if match:
            try:
                action = json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass  # malformed — ignore silently
            # Strip the tag from the visible text
            raw_reply = ACTION_PATTERN.sub("", raw_reply).strip()

        # 6. Persist the assistant reply
        await client.post(f"{DB_URL}/messages", json={
            "channel_id": req.channel_id,
            "role":       "assistant",
            "username":   "Planner",
            "content":    raw_reply,
        })

        # 7. If the model wants to create an event, save a pending record
        db_event_id = None
        if action and action.get("type") == "create_event":
            ev_resp = await client.post(f"{DB_URL}/events", json={
                "guild_id":    req.guild_id,
                "name":        action.get("name", "Unnamed Event"),
                "description": action.get("description", ""),
                "start_time":  action.get("start_time", ""),
            })
            if ev_resp.status_code == 201:
                db_event_id = ev_resp.json().get("id")

        return {
            "response":    raw_reply,
            "action":      action,
            "db_event_id": db_event_id,   # forwarded to the bot so it can PATCH after Discord confirms
        }


# ── Health ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model": OLLAMA_MODEL, "ollama": OLLAMA_URL}