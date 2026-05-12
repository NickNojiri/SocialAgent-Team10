"""
Container 1 — Discord Bot
- Listens for messages (either in a specific channel or when @mentioned).
- Forwards messages to the LLM service and posts the reply.
- If the LLM returns a create_event action, creates a Discord Scheduled Event
  and patches the DB record with the resulting Discord event ID.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import httpx
import json
from pathlib import Path

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("discord-bot")

# Quiet down the noisy discord.py internal loggers a bit
logging.getLogger("discord.client").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)

# ── Config ─────────────────────────────────────────────────────────────────
LLM_URL        = os.getenv("LLM_URL",       "http://llm:8001")
DB_URL         = os.getenv("DB_URL",        "http://db:8002")
BOT_CHANNEL_ID = int(os.getenv("BOT_CHANNEL_ID", "0"))   # 0 → respond to @mentions anywhere

# How long to wait between LLM health-check retries on startup
HEALTH_RETRY_SECONDS = 5
HEALTH_MAX_ATTEMPTS  = 12   # give up after ~1 minute

# ── Discord client setup ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True   # required to read message text

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Saved channels for discord servers
CONFIG_FILE = Path("channels.json")
ENABLED_CHANNELS: set[int] = set()

# ── Startup health check ───────────────────────────────────────────────────

async def wait_for_llm():
    """Poll the LLM /health endpoint until it responds or we give up."""
    log.info(f"Checking LLM service at {LLM_URL}/health …")
    async with httpx.AsyncClient(timeout=5.0) as client:
        for attempt in range(1, HEALTH_MAX_ATTEMPTS + 1):
            try:
                resp = await client.get(f"{LLM_URL}/health")
                data = resp.json()
                log.info(
                    f"✅ LLM service is up — model={data.get('model')} "
                    f"ollama={data.get('ollama')} backend={data.get('backend')}"
                )
                return True
            except Exception as exc:
                log.warning(
                    f"⏳ LLM not ready yet (attempt {attempt}/{HEALTH_MAX_ATTEMPTS}): {exc}"
                )
                if attempt < HEALTH_MAX_ATTEMPTS:
                    await asyncio.sleep(HEALTH_RETRY_SECONDS)

    log.error("❌ LLM service never became healthy — messages will fail until it does.")
    return False


# ── Events ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    log.info(f"=== Bot online: {bot.user} (ID: {bot.user.id}) ===")

    load_channels()

    try:
        synced = await tree.sync()
        log.info(f"[slash] Synced {len(synced)} slash commands")
    except Exception as exc:
        log.exception(f"[slash] Failed to sync commands: {exc}")

    if ENABLED_CHANNELS:
        log.info(f"Enabled channels: {sorted(ENABLED_CHANNELS)}")
    else:
        log.info("No enabled channels configured yet")

    # Run the health check in the background so the bot is already
    # connected to Discord while we wait for the LLM container to start.
    asyncio.create_task(wait_for_llm())


@bot.event
async def on_message(message: discord.Message):
    # Ignore our own messages and DMs
    if message.author.bot:
        return
    if message.guild is None:
        return

    bot_mentioned = bot.user.mentioned_in(message)
    in_enabled_channel = message.channel.id in ENABLED_CHANNELS

    if not (bot_mentioned or in_enabled_channel):
        return

    log.info(
        f"[msg] guild={message.guild.id} channel={message.channel.id} "
        f"user={message.author.display_name!r} | {message.content[:120]!r}"
    )

    t0 = time.perf_counter()
    async with message.channel.typing():
        try:
            response_text, action, db_event_id = await call_llm(message)
        except httpx.HTTPStatusError as exc:
            log.error(f"[llm] HTTP {exc.response.status_code} from LLM: {exc.response.text[:300]}")
            await message.channel.send(f"⚠️ LLM returned an error ({exc.response.status_code}). Check the logs.")
            return
        except httpx.RequestError as exc:
            log.error(f"[llm] Connection error: {exc}")
            await message.channel.send(
                "⚠️ Can't reach the LLM service right now. It may still be starting up — try again in a moment."
            )
            return
        except Exception as exc:
            log.exception(f"[llm] Unexpected error: {exc}")
            await message.channel.send(f"⚠️ Unexpected error: {exc}")
            return

    elapsed = time.perf_counter() - t0
    log.info(
        f"[llm] Response in {elapsed:.2f}s | action={action.get('type') if action else 'none'} "
        f"| reply={response_text[:80]!r}"
    )

    await message.channel.send(response_text)

    if action and action.get("type") == "create_event":
        await handle_create_event(message, action, db_event_id)


# ── Slash Commands ─────────────────────────────────────────────────────────

@tree.command(
    name="here",
    description="Enable this channel for bot conversations"
)
@app_commands.default_permissions(manage_channels=True)
async def here_command(interaction: discord.Interaction):

    ENABLED_CHANNELS.add(interaction.channel.id)
    save_channels()

    log.info(f"[config] Enabled channel {interaction.channel.id}")

    await interaction.response.send_message(
        "✅ This channel is now a bot-enabled meeting channel."
    )


@tree.command(
    name="leave",
    description="Disable bot conversations in this channel"
)
@app_commands.default_permissions(manage_channels=True)
async def leave_command(interaction: discord.Interaction):

    ENABLED_CHANNELS.discard(interaction.channel.id)
    save_channels()

    log.info(f"[config] Disabled channel {interaction.channel.id}")

    await interaction.response.send_message(
        "🛑 Bot responses disabled in this channel."
    )


@tree.command(
    name="channels",
    description="List enabled bot channels"
)
async def channels_command(interaction: discord.Interaction):

    if not ENABLED_CHANNELS:
        await interaction.response.send_message(
            "No bot-enabled channels configured."
        )
        return

    channel_mentions = [
        f"<#{channel_id}>"
        for channel_id in ENABLED_CHANNELS
    ]

    await interaction.response.send_message(
        "**Enabled Channels:**\n" + "\n".join(channel_mentions)
    )


# ── Helpers ────────────────────────────────────────────────────────────────

async def call_llm(message: discord.Message):
    """Send the message to the LLM service and return (text, action, db_event_id)."""
    payload = {
        "channel_id": str(message.channel.id),
        "guild_id":   str(message.guild.id),
        "user_id":    str(message.author.id),
        "username":   message.author.display_name,
        "message":    message.content,
    }
    log.debug(f"[llm] POST /chat payload: {payload}")

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{LLM_URL}/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()

    log.debug(f"[llm] Raw response: {data}")
    return data.get("response", "…"), data.get("action"), data.get("db_event_id")


def collect_ping_targets(message: discord.Message) -> list[discord.Member]:
    """
    Return a deduplicated list of members to ping after event creation:
    the message author + every @mentioned user (excluding the bot itself).
    """
    seen = set()
    targets = []
    for user in [message.author] + message.mentions:
        if user.id not in seen and user.id != bot.user.id:
            seen.add(user.id)
            targets.append(user)
    return targets


def load_channels():
    global ENABLED_CHANNELS

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            ENABLED_CHANNELS = set(int(x) for x in data)
            log.info(f"[config] Loaded {len(ENABLED_CHANNELS)} enabled channels")
        except Exception as exc:
            log.warning(f"[config] Failed to load channels.json: {exc}")


def save_channels():
    try:
        CONFIG_FILE.write_text(
            json.dumps(list(ENABLED_CHANNELS), indent=2)
        )
    except Exception as exc:
        log.warning(f"[config] Failed to save channels.json: {exc}")


# ── Schedule ────────────────────────────────────────────────────────────────

async def handle_create_event(
    message: discord.Message,
    action: dict,
    db_event_id: int | None,
):
    """Create a Discord Scheduled Event, then ping the author and all @mentioned users."""
    log.info(f"[event] Creating Discord scheduled event: {action}")
    try:
        from datetime import timedelta
        raw_time = action.get("start_time", "")
        start_dt = datetime.fromisoformat(raw_time).replace(tzinfo=timezone.utc)

        # end_time is required by Discord for external events.
        # Use what the LLM provided; fall back to start + 2 hours.
        raw_end = action.get("end_time", "")
        end_dt  = (
            datetime.fromisoformat(raw_end).replace(tzinfo=timezone.utc)
            if raw_end else start_dt + timedelta(hours=2)
        )
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=2)
            log.warning("[event] end_time was not after start_time -- defaulting to +2 hours")

        discord_event = await message.guild.create_scheduled_event(
            name          = action.get("name", "Event")[:100],
            description   = action.get("description", "")[:1000],
            start_time    = start_dt,
            end_time      = end_dt,
            entity_type   = discord.EntityType.external,
            location      = action.get("location", "TBD"),
            privacy_level = discord.PrivacyLevel.guild_only,
        )

        log.info(f"[event] Discord event created: id={discord_event.id} name={discord_event.name!r}")

        # Build the ping string: author + all @mentioned users
        targets     = collect_ping_targets(message)
        ping_str    = " ".join(u.mention for u in targets)
        names_str   = ", ".join(u.display_name for u in targets)
        log.info(f"[event] Pinging {len(targets)} user(s): {names_str}")

        await message.channel.send(
            f"📅 **Event scheduled!** {ping_str}\n"
            f"**{discord_event.name}** — <t:{int(start_dt.timestamp())}:F> "
            f"to <t:{int(end_dt.timestamp())}:t>\n"
            f"{discord_event.url}"
        )

        if db_event_id:
            async with httpx.AsyncClient(timeout=10.0) as client:
                patch_resp = await client.patch(
                    f"{DB_URL}/events/{db_event_id}",
                    json={"discord_event_id": str(discord_event.id)},
                )
                log.info(f"[db] Patched event {db_event_id} → discord_event_id={discord_event.id} ({patch_resp.status_code})")

    except ValueError as exc:
        log.warning(f"[event] Bad start_time in action: {exc} | raw={action.get('start_time')!r}")
        await message.channel.send(
            "⚠️ I tried to create an event but the start time was unclear. "
            "Try again with a specific date and time!"
        )
    except discord.Forbidden:
        log.error("[event] Missing 'Manage Events' permission")
        await message.channel.send(
            "⚠️ I don't have permission to create scheduled events. "
            "Please give me the **Manage Events** permission!"
        )
    except Exception as exc:
        log.exception(f"[event] Unexpected error: {exc}")
        await message.channel.send(f"⚠️ Couldn't create the Discord event: {exc}")


# ── Run ────────────────────────────────────────────────────────────────────

token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN env var is not set!")

log.info("Starting Discord bot …")
bot.run(token, log_handler=None)   # log_handler=None lets our config above own the output