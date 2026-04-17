# Event Planner — Docker Prototype

A 3-container event coordination assistant powered by Discord + Ollama.

```
┌─────────────────────────────────────────────────────────────┐
│  Discord                                                     │
│   User sends message                                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │   Container 1           │
          │   discord-bot (app/)    │  discord.py
          │   Receives messages     │
          │   Posts replies         │
          │   Creates Discord events│
          └────────────┬────────────┘
                       │  HTTP POST /chat
          ┌────────────▼────────────┐
          │   Container 2           │
          │   llm (llm/)            │  FastAPI + httpx
          │   Builds prompt         │
          │   Calls Ollama          │
          │   Parses actions        │
          └──────┬──────────┬───────┘
    /messages   │          │  /events
    /messages   │          │  (save pending event)
          ┌─────▼──────────▼───────┐
          │   Container 3          │
          │   db (db/)             │  FastAPI + SQLite
          │   Conversation memory  │
          │   Event records        │
          └────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │   HOST MACHINE          │
          │   Ollama (port 11434)   │  llama3 / any model
          └─────────────────────────┘
```

---

## Quick Start

### 1. Prerequisites

- Docker + Docker Compose
- [Ollama](https://ollama.com) running on your host with at least one model pulled:
  ```bash
  ollama pull llama3
  ```

### 2. Create a Discord Bot

1. Go to https://discord.com/developers/applications → **New Application**
2. **Bot** tab → **Add Bot** → copy the **Token**
3. Under **Privileged Gateway Intents**, enable **Message Content Intent**
4. **OAuth2 → URL Generator**: scopes = `bot`, bot permissions:
   - Read Messages / View Channels
   - Send Messages
   - Manage Events ← required for creating Discord scheduled events
5. Open the generated URL and invite the bot to your server

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```
DISCORD_TOKEN=your-actual-token-here
OLLAMA_MODEL=llama3          # or mistral, phi3, etc.

# Optional: lock the bot to one channel ID
# BOT_CHANNEL_ID=1234567890
```

### 4. Run

```bash
docker compose up --build
```

That's it. The bot will appear online in Discord.

---

## Usage

**Chat with the bot** — either @mention it in any channel, or post in the channel set by `BOT_CHANNEL_ID`.

**Schedule an event** — just ask naturally:
> @Planner let's do a game night this Friday at 8pm

The bot will:
1. Reply with a friendly confirmation
2. Automatically create a **Discord Scheduled Event** in your server
3. Post the event link in the channel

**Ask about upcoming events:**
> @Planner what events do we have coming up?

---

## Project Structure

```
event-planner/
├── docker-compose.yml
├── .env.example
├── app/                  # Container 1 — Discord bot
│   ├── Dockerfile
│   ├── requirements.txt
│   └── bot.py
├── llm/                  # Container 2 — LLM orchestration
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py
└── db/                   # Container 3 — SQLite memory
    ├── Dockerfile
    ├── requirements.txt
    └── app.py
```

---

## Swapping the LLM

Change `OLLAMA_MODEL` in `.env` to any model you have pulled locally:

```
OLLAMA_MODEL=mistral
OLLAMA_MODEL=phi3
OLLAMA_MODEL=gemma2
```

Restart with `docker compose restart llm`.

---

## Extending Later

| Feature | Where to add |
|---|---|
| Twilio SMS reminders | `app/bot.py` — trigger on event creation |
| Auth / user profiles | New container or extend `db/app.py` |
| Google Calendar sync | `llm/app.py` — add a new action type |
| Web dashboard | New container, reads from `db` service |
| Cloud LLM fallback | `llm/app.py` — swap Ollama call for OpenAI/Anthropic |

---

## Troubleshooting

**How do I start the project**
```bash
docker compose up --build
```

**Bot doesn't respond**
- Check `DISCORD_TOKEN` is correct
- Confirm **Message Content Intent** is enabled in the Developer Portal

**"Ollama error: ..."**
- Make sure Ollama is running on your host: `ollama serve`
- Confirm the model exists: `ollama list`

**"I don't have permission to create scheduled events"**
- Re-invite the bot with the **Manage Events** permission checked

