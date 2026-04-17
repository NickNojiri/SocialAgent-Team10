"""
Container 1 — Discord Bot
- Listens for messages (either in a specific channel or when @mentioned).
- Forwards messages to the LLM service and posts the reply.
- If the LLM returns a create_event action, creates a Discord Scheduled Event
  and patches the DB record with the resulting Discord event ID.
"""

import os
from datetime import datetime, timezone

import discord
import httpx

LLM_URL       = os.getenv("LLM_URL", "http://llm:8001")
DB_URL        = os.getenv("DB_URL",  "http://db:8002")
BOT_CHANNEL_ID = int(os.getenv("BOT_CHANNEL_ID", "0"))  # 0 → respond to @mentions anywhere


# ── Discord client setup ───────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True   # required to read message text

bot = discord.Client(intents=intents)


# ── Events ─────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[bot] Logged in as {bot.user} (ID: {bot.user.id})")
    if BOT_CHANNEL_ID:
        print(f"[bot] Listening in channel {BOT_CHANNEL_ID}")
    else:
        print("[bot] Listening for @mentions in all channels")


@bot.event
async def on_message(message: discord.Message):
    # Ignore our own messages
    if message.author.bot:
        return
    if message.guild is None:
        return  # ignore DMs for now

    bot_mentioned     = bot.user.mentioned_in(message)
    in_target_channel = (BOT_CHANNEL_ID == 0 or message.channel.id == BOT_CHANNEL_ID)

    if not (bot_mentioned or in_target_channel):
        return

    # Show a typing indicator while we wait for the LLM
    async with message.channel.typing():
        try:
            response_text, action, db_event_id = await call_llm(message)
        except Exception as exc:
            await message.channel.send(f"⚠️ LLM service error: {exc}")
            return

    # Post the assistant's text reply
    await message.channel.send(response_text)

    # Handle create_event action
    if action and action.get("type") == "create_event":
        await handle_create_event(message, action, db_event_id)


# ── Helpers ────────────────────────────────────────────────────────────────

async def call_llm(message: discord.Message):
    """Send the message to the LLM service and return (text, action, db_event_id)."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{LLM_URL}/chat",
            json={
                "channel_id": str(message.channel.id),
                "guild_id":   str(message.guild.id),
                "user_id":    str(message.author.id),
                "username":   message.author.display_name,
                "message":    message.content,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", "…"), data.get("action"), data.get("db_event_id")


async def handle_create_event(
    message: discord.Message,
    action: dict,
    db_event_id: int | None,
):
    """Create a Discord Scheduled Event and optionally update the DB record."""
    try:
        raw_time = action.get("start_time", "")
        start_dt = datetime.fromisoformat(raw_time).replace(tzinfo=timezone.utc)

        discord_event = await message.guild.create_scheduled_event(
            name          = action.get("name", "Event")[:100],
            description   = action.get("description", "")[:1000],
            start_time    = start_dt,
            entity_type   = discord.EntityType.external,
            location      = "TBD",
            privacy_level = discord.PrivacyLevel.guild_only,
        )

        await message.channel.send(
            f"📅 **Discord event created!**\n"
            f"**{discord_event.name}** — <t:{int(start_dt.timestamp())}:F>\n"
            f"{discord_event.url}"
        )

        # Patch the DB so we have the Discord event ID stored
        if db_event_id:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.patch(
                    f"{DB_URL}/events/{db_event_id}",
                    json={"discord_event_id": str(discord_event.id)},
                )

    except ValueError:
        await message.channel.send(
            "⚠️ I tried to create an event but the start time was unclear. "
            "Try again with a specific date and time!"
        )
    except discord.Forbidden:
        await message.channel.send(
            "⚠️ I don't have permission to create scheduled events. "
            "Please give me the **Manage Events** permission!"
        )
    except Exception as exc:
        await message.channel.send(f"⚠️ Couldn't create the Discord event: {exc}")


# ── Run ────────────────────────────────────────────────────────────────────

bot.run(os.getenv("DISCORD_TOKEN"))