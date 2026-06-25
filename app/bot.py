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
from datetime import timedelta

import httpx
import json
from pathlib import Path

import cards

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
LLM_URL = os.getenv("LLM_URL", "http://llm:8001")
DB_URL = os.getenv("DB_URL", "http://db:8002")
RECOMMEND_URL = os.getenv("RECOMMEND_URL", "http://recommend:8003")   # Phase 6 retrieval service
# Reel capture → the host admin app (ingest + shared votes). In Docker the bot
# reaches the host via host.docker.internal; use localhost when run directly.
INGEST_URL = os.getenv("INGEST_URL", "http://host.docker.internal:8010")
ADMIN_URL = os.getenv("ADMIN_URL", INGEST_URL)
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
# Channels opted into automatic chat-context event suggestions (Phase 6, in-memory, off by default)
SUGGESTION_CHANNELS: set[int] = set()
# Channels designated as reel "drop zones" — any IG link posted here is auto-captured.
DROP_CHANNELS: set[int] = set()

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
    cards.register_dynamic_items(bot)   # keep spot buttons alive across restarts

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
    if message.author.bot:
        return

    content = message.content or ""
    urls = cards.extract_ig_urls(content)
    is_dm = message.guild is None
    bot_mentioned = bot.user is not None and bot.user.mentioned_in(message)
    in_drop_channel = message.channel.id in DROP_CHANNELS
    in_enabled_channel = message.channel.id in ENABLED_CHANNELS

    # ── Reel capture: a DM, a #drop-reels channel, or an @mention with a link.
    # No command, no setup — the user just pastes a reel.
    if urls and (is_dm or in_drop_channel or bot_mentioned):
        await handle_reel_capture(message, urls)
        return

    # A DM with no link → gentle onboarding so the user knows what to do.
    if is_dm:
        await message.channel.send(embed=_welcome_embed())
        return

    # ── Existing guild conversation path: @mention or enabled channel only.
    if not (bot_mentioned or in_enabled_channel):
        return

    # Add the mentioned bot in the channel to enabled channel
    if (bot_mentioned and not in_enabled_channel):
        ENABLED_CHANNELS.add(message.channel.id)

    log.info(
        f"[msg] guild={message.guild.id} channel={message.channel.id} "
        f"user={message.author.display_name!r} | {content[:120]!r}"
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

    # Phase 6: opt-in chat-context event suggestions. When intent is detected the
    # recommendations are posted in a thread on the user's message so the main
    # channel stays clean. Best-effort — never breaks the bot.
    if message.channel.id in SUGGESTION_CHANNELS:
        try:
            data = await call_recommend(message.channel.id, message.content, "auto")
            if not data.get("suppressed") and data.get("recommendations"):
                thread = await message.create_thread(name="Spot suggestions")
                await thread.send(data["markdown"])
        except Exception as exc:
            log.debug(f"[recommend] auto-suggest skipped: {exc}")


# ── Reel capture ─────────────────────────────────────────────────────────────

async def handle_reel_capture(message: discord.Message, urls: list[str]):
    """Paste-and-go: ack with a reaction, ingest the reel(s), reply with cards."""
    await _add_reaction(message, "⏳")
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(f"{INGEST_URL}/api/ingest", json={"urls": urls})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning(f"[capture] ingest failed: {exc}")
        await _swap_reaction(message, "⏳", "⚠️")
        await message.reply(
            "⚠️ Couldn't reach the catalog service — is the admin app running?",
            mention_author=False,
        )
        return

    events = data.get("events", [])
    await _swap_reaction(message, "⏳", "✅" if events else "⚠️")

    if not events:
        await message.reply(_capture_failure_text(data), mention_author=False)
        return

    sharer = message.author.display_name
    if len(events) > 3:   # a big paste → one tidy summary instead of N cards
        await message.reply(embed=_summary_embed(events, sharer), mention_author=False)
        return

    for event in events:
        event["sharer"] = sharer
        event["already"] = not event.get("new", True)
        await message.reply(
            embed=cards.build_spot_embed(event),
            view=cards.build_spot_view(event["id"], int(event.get("votes", 0))),
            mention_author=False,
        )


async def _add_reaction(message: discord.Message, emoji: str):
    try:
        await message.add_reaction(emoji)
    except Exception:
        pass


async def _swap_reaction(message: discord.Message, old: str, new: str):
    try:
        await message.remove_reaction(old, bot.user)
    except Exception:
        pass
    await _add_reaction(message, new)


def _capture_failure_text(data: dict) -> str:
    if data.get("unreadable"):
        return "🚫 I couldn't read that reel — it may be private or removed."
    return "🤔 I read it, but couldn't find a venue worth saving."


def _summary_embed(events: list[dict], sharer: str) -> discord.Embed:
    embed = discord.Embed(title=f"✅ Added {len(events)} spots", color=0x3FB950)
    lines = []
    for event in events[:10]:
        tag = "" if event.get("new", True) else " · already saved"
        lines.append(f"{cards.emoji_for(event.get('category'))} **{event.get('venue')}**{tag}")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"shared by {sharer} · vote on each with /catalog")
    return embed


def _welcome_embed() -> discord.Embed:
    return discord.Embed(
        title="👋 Drop a reel, get a spot",
        description=(
            "Paste any Instagram reel or post link here — no commands needed.\n"
            "I'll catalog the venue and post a card you can 👍 to vote up."
        ),
        color=0x6EA8FE,
    )


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


# ── Recommendation commands (Phase 6) ───────────────────────────────────────

@tree.command(
    name="events",
    description="Find event inspirations matching a vibe (e.g. 'late night tacos')",
)
@app_commands.describe(vibe="What are you in the mood for?")
async def events_command(interaction: discord.Interaction, vibe: str):
    await interaction.response.defer(thinking=True)
    try:
        data = await call_recommend(interaction.channel_id, vibe, "command")
        await interaction.followup.send(data.get("markdown") or "No matches found.")
    except Exception as exc:
        log.warning(f"[recommend] /events failed: {exc}")
        await interaction.followup.send(
            "⚠️ Couldn't reach the recommendation service right now."
        )


@tree.command(
    name="suggestions",
    description="Toggle automatic event suggestions in this channel",
)
@app_commands.describe(state="on or off")
async def suggestions_command(interaction: discord.Interaction, state: str):
    if state.strip().lower() in ("on", "enable", "true"):
        SUGGESTION_CHANNELS.add(interaction.channel_id)
        msg = "✅ Automatic event suggestions enabled in this channel."
    else:
        SUGGESTION_CHANNELS.discard(interaction.channel_id)
        msg = "🛑 Automatic event suggestions disabled in this channel."
    log.info(f"[config] suggestions {state!r} for channel {interaction.channel_id}")
    await interaction.response.send_message(msg)


# ── Reel capture commands ────────────────────────────────────────────────────

@tree.command(
    name="dropchannel",
    description="Auto-capture any Instagram reel link posted in this channel",
)
@app_commands.describe(state="on or off")
@app_commands.default_permissions(manage_channels=True)
async def dropchannel_command(interaction: discord.Interaction, state: str):
    if state.strip().lower() in ("on", "enable", "true"):
        DROP_CHANNELS.add(interaction.channel_id)
        msg = "✅ This is now a reel drop channel — just paste links, no commands needed."
    else:
        DROP_CHANNELS.discard(interaction.channel_id)
        msg = "🛑 This channel is no longer auto-capturing reels."
    save_channels()
    log.info(f"[config] dropchannel {state!r} for channel {interaction.channel_id}")
    await interaction.response.send_message(msg)


@tree.command(name="help", description="How to save spots with the bot")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_welcome_embed(), ephemeral=True)


@tree.command(name="catalog", description="Show the top-voted spots in the catalog")
async def catalog_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{INGEST_URL}/api/events")
            resp.raise_for_status()
            events = resp.json().get("events", [])
    except Exception as exc:
        log.warning(f"[catalog] fetch failed: {exc}")
        await interaction.followup.send("⚠️ Couldn't reach the catalog right now.")
        return
    if not events:
        await interaction.followup.send("The catalog is empty — paste a reel to start it!")
        return
    lines = [
        f"{cards.emoji_for(ev.get('category'))} **{ev.get('venue')}** · {int(ev.get('votes', 0))} 👍"
        for ev in events[:10]
    ]
    await interaction.followup.send("🏆 **Top spots**\n" + "\n".join(lines))


# ── Helpers ────────────────────────────────────────────────────────────────

async def call_recommend(channel_id: int, message: str, mode: str) -> dict:
    """Query the recommendation service (mirrors call_llm's HTTP pattern)."""
    payload = {"channel_id": str(channel_id), "message": message, "mode": mode}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{RECOMMEND_URL}/recommend", json=payload)
        resp.raise_for_status()
        return resp.json()


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
    global ENABLED_CHANNELS, DROP_CHANNELS

    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            if isinstance(data, list):           # legacy format: a bare list of enabled channels
                ENABLED_CHANNELS = set(int(x) for x in data)
            else:
                ENABLED_CHANNELS = set(int(x) for x in data.get("enabled", []))
                DROP_CHANNELS = set(int(x) for x in data.get("drop", []))
            log.info(
                f"[config] Loaded {len(ENABLED_CHANNELS)} enabled, {len(DROP_CHANNELS)} drop channels"
            )
        except Exception as exc:
            log.warning(f"[config] Failed to load channels.json: {exc}")


def save_channels():
    try:
        CONFIG_FILE.write_text(
            json.dumps({"enabled": list(ENABLED_CHANNELS), "drop": list(DROP_CHANNELS)}, indent=2)
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
        PST = timezone(timedelta(hours=-8))

        raw_start = action.get("start_time", "")
        raw_end = action.get("end_time", "")

        start_dt = datetime.fromisoformat(raw_start).replace(tzinfo=timezone.utc).astimezone(PST)

        end_dt = (
            datetime.fromisoformat(raw_end).replace(tzinfo=timezone.utc).astimezone(PST)
            if raw_end
            else start_dt + timedelta(hours=2)
        )

        # end_time is required by Discord for external events.
        # Use what the LLM provided; fall back to start + 2 hours.
        if end_dt <= start_dt:
            log.warning("[event] end_time was not after start_time — defaulting to +2 hours")
            end_dt = start_dt + timedelta(hours=2)

        true_start_time = start_dt + timedelta(hours=7)
        true_end_time = end_dt + timedelta(hours=7)
        
        discord_event = await message.guild.create_scheduled_event(
            name          = action.get("name", "Event")[:100],
            description   = action.get("description", "")[:1000],
            start_time    = true_start_time,
            end_time      = true_end_time,
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
            f"**{discord_event.name}** — <t:{int(true_start_time.timestamp())}:F> "
            f"to <t:{int(true_end_time.timestamp())}:t>\n"
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