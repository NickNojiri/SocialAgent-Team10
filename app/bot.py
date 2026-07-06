"""
SpotBot — the Discord bot.

Paste an Instagram reel anywhere the bot can read and it becomes a votable
"spot card" (no command needed). Votes reach a quorum → the bot offers to lock
the outing in as a Discord Scheduled Event. /plan reads the recent chat and
pitches saved spots that match the vibe.

The bot is deliberately thin: capture, cards, and votes are HTTP calls to the
admin app (:8010); recommendations come from the recommend service (:8003).
"""

import logging
import os
import ssl

# MUST run before importing discord/aiohttp — aiohttp builds a verified SSL context
# at import time, so the default context has to be overridden first. On TLS-
# intercepting networks the proxy CA fails OpenSSL; BOT_INSECURE_SSL=1 skips it.
if os.getenv("BOT_INSECURE_SSL") == "1":
    _orig_ssl_ctx = ssl.create_default_context

    def _insecure_ssl_ctx(*args, **kwargs):
        ctx = _orig_ssl_ctx(*args, **kwargs)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    ssl.create_default_context = _insecure_ssl_ctx
    ssl._create_default_https_context = _insecure_ssl_ctx

import json
from pathlib import Path
from typing import Literal

import discord
import httpx
from discord import app_commands

import cards

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("discord-bot")

logging.getLogger("discord.client").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.http").setLevel(logging.WARNING)

# ── Config ─────────────────────────────────────────────────────────────────
RECOMMEND_URL = os.getenv("RECOMMEND_URL", "http://recommend:8003")
# Reel capture → the host admin app (ingest + shared votes). In Docker the bot
# reaches the host via host.docker.internal; use localhost when run directly.
INGEST_URL = os.getenv("INGEST_URL", "http://host.docker.internal:8010")
ADMIN_URL = os.getenv("ADMIN_URL", INGEST_URL)

if os.getenv("BOT_INSECURE_SSL") == "1":
    log.warning("[ssl] cert verification disabled for the intercepting proxy (BOT_INSECURE_SSL=1)")

# ── Discord client setup ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True   # required to read pasted links

bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# Capture works EVERYWHERE the bot can read, by default. Config is opt-out:
#   muted        — channels where link capture is turned off
#   suggestions  — channels opted into ambient chat-context suggestions
#   tipped       — guilds that already saw the one-time first-card tip
CONFIG_FILE = Path("channels.json")
MUTED_CHANNELS: set[int] = set()
SUGGESTION_CHANNELS: set[int] = set()
TIPPED_GUILDS: set[int] = set()


# ── Events ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    log.info(f"=== Bot online: {bot.user} (ID: {bot.user.id}) ===")

    load_config()
    cards.register_dynamic_items(bot)   # keep spot buttons alive across restarts

    try:
        synced = await tree.sync()
        log.info(f"[slash] Synced {len(synced)} slash commands")
    except Exception as exc:
        log.exception(f"[slash] Failed to sync commands: {exc}")

    log.info(
        f"Capture: everywhere except {len(MUTED_CHANNELS)} muted channel(s) · "
        f"suggestions in {len(SUGGESTION_CHANNELS)} channel(s)"
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    urls = cards.extract_ig_urls(message.content or "")

    # ── Capture: any IG link, anywhere we can read — unless the channel is muted.
    if urls:
        if message.channel.id in MUTED_CHANNELS:
            return
        await handle_reel_capture(message, urls)
        return

    # A DM with no link → gentle onboarding so the user knows what to do.
    if message.guild is None:
        await message.channel.send(embed=_welcome_embed())
        return

    # Ambient suggestions (opt-in per channel): when the chat sounds like a
    # request ("we want tacos"), pitch matching saved spots in a thread.
    if message.channel.id in SUGGESTION_CHANNELS:
        try:
            data = await call_recommend(
                message.channel.id, message.content, "auto", cards.guild_key(message)
            )
            if not data.get("suppressed") and data.get("recommendations"):
                thread = await message.create_thread(name="Spot suggestions")
                await thread.send(data["markdown"])
        except Exception as exc:
            log.debug(f"[recommend] auto-suggest skipped: {exc}")


# ── Reel capture ─────────────────────────────────────────────────────────────

async def handle_reel_capture(message: discord.Message, urls: list[str]):
    """Paste-and-go with visible progress: a status reply that updates through
    the capture, cards on success, and Retry / Add-manually on failure."""
    await _add_reaction(message, "⏳")
    noun = "that reel" if len(urls) == 1 else f"those {len(urls)} links"
    status = await message.reply(
        f"🔎 Reading {noun} — caption, audio, and location. Takes ~30s…",
        mention_author=False,
    )

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(
                f"{INGEST_URL}/api/ingest",
                json={"urls": urls, "guild_id": cards.guild_key(message)},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning(f"[capture] ingest failed: {exc}")
        await _swap_reaction(message, "⏳", "⚠️")
        await status.edit(
            content="⚠️ Couldn't reach the catalog service — is the admin app running?",
            view=cards.build_failure_view(urls[0] if len(urls) == 1 else None),
        )
        return

    events = data.get("events", [])
    await _swap_reaction(message, "⏳", "✅" if events else "⚠️")

    if not events:
        await status.edit(
            content=_capture_failure_text(data),
            view=cards.build_failure_view(urls[0] if len(urls) == 1 else None),
        )
        return

    sharer = message.author.display_name
    if len(events) > 3:   # a big paste → one tidy summary instead of N cards
        await status.edit(content=None, embed=_summary_embed(events, sharer))
    else:
        first, rest = events[0], events[1:]
        first["sharer"] = sharer
        first["already"] = not first.get("new", True)
        # The status message *becomes* the first card — progress turns into payoff.
        await status.edit(
            content=None,
            embed=cards.build_spot_embed(first),
            view=cards.build_spot_view(first["id"], int(first.get("votes", 0))),
        )
        for event in rest:
            event["sharer"] = sharer
            event["already"] = not event.get("new", True)
            await message.reply(
                embed=cards.build_spot_embed(event),
                view=cards.build_spot_view(event["id"], int(event.get("votes", 0))),
                mention_author=False,
            )

    await _maybe_first_card_tip(message)


async def _maybe_first_card_tip(message: discord.Message):
    """One sentence, once per server, right when it's most relevant."""
    guild = message.guild
    if guild is None or guild.id in TIPPED_GUILDS:
        return
    TIPPED_GUILDS.add(guild.id)
    save_config()
    try:
        await message.channel.send(
            f"💡 **First spot saved!** Anyone can tap 👍 — at {cards.QUORUM} the bot offers to "
            f"put it on the server calendar. When you've saved a few, try `/plan`."
        )
    except Exception:
        pass


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
        return "🚫 I couldn't read that reel — it may be private or removed. Retry, or add the spot yourself:"
    return "🤔 I read it, but couldn't find a venue worth saving. Add it yourself if I missed it:"


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
            "Paste any Instagram reel or post link — here or in any server channel "
            "I can read. No commands needed.\n"
            "I'll catalog the venue and post a card your friends can 👍 to vote up."
        ),
        color=0x6EA8FE,
    )


# ── Slash Commands ─────────────────────────────────────────────────────────

@tree.command(name="setup", description="How SpotBot works here + current settings")
async def setup_command(interaction: discord.Interaction):
    muted_here = interaction.channel_id in MUTED_CHANNELS
    embed = discord.Embed(
        title="🧭 SpotBot in 20 seconds",
        color=0x6EA8FE,
        description=(
            "**1. Paste** any Instagram reel link in any channel — I turn it into a votable spot card.\n"
            f"**2. Vote** with 👍 — at **{cards.QUORUM}** I offer to put it on the server calendar.\n"
            "**3. Plan** with `/plan` — I read the recent chat and pitch saved spots that match.\n\n"
            "Capture is on everywhere by default. Use `/mute` in a channel to turn it off there."
        ),
    )
    embed.add_field(
        name="This channel",
        value=("🔇 capture muted" if muted_here else "🎬 capturing links")
        + (" · 💬 ambient suggestions on" if interaction.channel_id in SUGGESTION_CHANNELS else ""),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="mute", description="Toggle reel capture in this channel")
@app_commands.default_permissions(manage_channels=True)
async def mute_command(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    if channel_id in MUTED_CHANNELS:
        MUTED_CHANNELS.discard(channel_id)
        msg = "🎬 Capture is back on — pasted reels become cards here again."
    else:
        MUTED_CHANNELS.add(channel_id)
        msg = "🔇 Capture muted here — I'll ignore pasted links in this channel."
    save_config()
    log.info(f"[config] mute toggled for channel {channel_id}")
    await interaction.response.send_message(msg)


@tree.command(name="suggestions", description="Toggle automatic spot suggestions in this channel")
@app_commands.default_permissions(manage_channels=True)
async def suggestions_command(interaction: discord.Interaction, state: Literal["on", "off"]):
    if state == "on":
        SUGGESTION_CHANNELS.add(interaction.channel_id)
        msg = "✅ I'll suggest saved spots in a thread when the chat sounds like a plan."
    else:
        SUGGESTION_CHANNELS.discard(interaction.channel_id)
        msg = "🛑 Automatic suggestions off in this channel."
    save_config()
    log.info(f"[config] suggestions {state!r} for channel {interaction.channel_id}")
    await interaction.response.send_message(msg)


@tree.command(
    name="events",
    description="Find saved spots matching a vibe (e.g. 'late night tacos')",
)
@app_commands.describe(vibe="What are you in the mood for?")
async def events_command(interaction: discord.Interaction, vibe: str):
    await interaction.response.defer(thinking=True)
    try:
        data = await call_recommend(
            interaction.channel_id, vibe, "command", cards.guild_key(interaction)
        )
        await interaction.followup.send(data.get("markdown") or "No matches found.")
    except Exception as exc:
        log.warning(f"[recommend] /events failed: {exc}")
        await interaction.followup.send(
            "⚠️ Couldn't reach the recommendation service right now."
        )


@tree.command(name="help", description="How to save spots with the bot")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_welcome_embed(), ephemeral=True)


@tree.command(name="catalog", description="Show the top-voted spots in the catalog")
async def catalog_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{INGEST_URL}/api/events",
                params={"guild_id": cards.guild_key(interaction)},
            )
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


# ── Planning ─────────────────────────────────────────────────────────────────

@tree.command(
    name="plan",
    description="Read the recent chat and plan an outing together",
)
async def plan_command(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    # Gather the recent multi-person chat (skip the bot's own messages).
    lines: list[str] = []
    async for m in interaction.channel.history(limit=25):
        if m.author.bot or not m.content.strip():
            continue
        lines.append(f"{m.author.display_name}: {m.content.strip()}")
    lines.reverse()
    transcript = "\n".join(lines)[:4000]
    if not transcript:
        await interaction.followup.send(
            "There's not much to go on yet — chat about what you're feeling, then `/plan` again."
        )
        return

    try:
        data = await call_plan(interaction.channel_id, transcript, cards.guild_key(interaction))
    except Exception as exc:
        log.warning(f"[plan] failed: {exc}")
        await interaction.followup.send("⚠️ Couldn't reach the planner right now.")
        return

    request = data.get("request", {})
    recs = data.get("recommendations", [])

    thread = await _open_plan_thread(interaction, request)
    target = thread or interaction.channel
    await target.send(embed=_what_i_heard_embed(request))
    if not recs:
        await target.send("I couldn't find a match yet — add a vibe or widen the area, then `/plan` again.")
    else:
        for rec in recs:
            event = {
                "id": rec.get("content_hash"),
                "venue": rec.get("venue_name"),
                "category": rec.get("category"),
                "theme": rec.get("core_theme"),
                "source_url": rec.get("source_url"),
                "start_epoch": rec.get("start_epoch"),
                "end_epoch": rec.get("end_epoch"),
                "votes": 0,
            }
            await target.send(
                embed=cards.build_spot_embed(event),
                view=cards.build_spot_view(event["id"], 0),
            )
    if thread is not None:
        await interaction.followup.send(f"📋 I put a plan together in {thread.mention} — vote on the picks!")
    else:
        await interaction.followup.send("📋 Here's a plan — vote on the picks above!")


# ── Helpers ────────────────────────────────────────────────────────────────

async def call_recommend(channel_id: int, message: str, mode: str, guild_id: str = "") -> dict:
    """Query the recommendation service (mirrors the capture HTTP pattern)."""
    payload = {
        "channel_id": str(channel_id),
        "message": message,
        "mode": mode,
        "guild_id": guild_id,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{RECOMMEND_URL}/recommend", json=payload)
        resp.raise_for_status()
        return resp.json()


async def call_plan(channel_id: int, transcript: str, guild_id: str = "") -> dict:
    """Ask the recommend service to synthesize the group's request + a shortlist."""
    payload = {"channel_id": str(channel_id), "transcript": transcript, "guild_id": guild_id}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(f"{RECOMMEND_URL}/plan", json=payload)
        resp.raise_for_status()
        return resp.json()


def _what_i_heard_embed(request: dict) -> discord.Embed:
    """The 'here's what I heard' confirmation card for /plan."""
    embed = discord.Embed(
        title="📋 Here's what I heard",
        description="Vote on the picks below — or keep chatting and `/plan` again to refine.",
        color=0x6EA8FE,
    )
    fields = [
        ("🍽️ vibe", request.get("vibe")),
        ("📍 area", request.get("area")),
        ("🧭 midpoint of", request.get("midpoint_of")),
        ("📍 near", request.get("near")),
        ("💸 budget", request.get("budget")),
        ("🕗 when", request.get("time")),
    ]
    shown = False
    for name, val in fields:
        if val:
            embed.add_field(name=name, value=str(val), inline=True)
            shown = True
    if not shown:
        embed.add_field(name="🍽️ vibe", value="something fun", inline=True)
    return embed


async def _open_plan_thread(interaction: discord.Interaction, request: dict):
    """Plans live in a thread so the main channel stays clean. Best-effort."""
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return None
    vibe = request.get("vibe") or "outing"
    name = f"Plan: {vibe}"
    try:
        return await channel.create_thread(name=name[:90], type=discord.ChannelType.public_thread)
    except Exception as exc:
        log.debug(f"[plan] thread creation skipped: {exc}")
        return None


# ── Config persistence ──────────────────────────────────────────────────────

def load_config():
    global MUTED_CHANNELS, SUGGESTION_CHANNELS, TIPPED_GUILDS

    if not CONFIG_FILE.exists():
        return
    try:
        data = json.loads(CONFIG_FILE.read_text())
        if isinstance(data, dict):
            MUTED_CHANNELS = set(int(x) for x in data.get("muted", []))
            SUGGESTION_CHANNELS = set(int(x) for x in data.get("suggestions", []))
            TIPPED_GUILDS = set(int(x) for x in data.get("tipped", []))
        # Legacy formats (a bare list, or {"enabled": [...], "drop": [...]})
        # described channel *enabling* — obsolete now that capture is opt-out,
        # so they migrate to a clean slate on the first save.
        log.info(
            f"[config] Loaded {len(MUTED_CHANNELS)} muted, "
            f"{len(SUGGESTION_CHANNELS)} suggestion channel(s)"
        )
    except Exception as exc:
        log.warning(f"[config] Failed to load channels.json: {exc}")


def save_config():
    try:
        CONFIG_FILE.write_text(
            json.dumps(
                {
                    "muted": list(MUTED_CHANNELS),
                    "suggestions": list(SUGGESTION_CHANNELS),
                    "tipped": list(TIPPED_GUILDS),
                },
                indent=2,
            )
        )
    except Exception as exc:
        log.warning(f"[config] Failed to save channels.json: {exc}")


# ── Run ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN env var is not set!")

    log.info("Starting Discord bot …")
    bot.run(token, log_handler=None)   # log_handler=None lets our config own the output
