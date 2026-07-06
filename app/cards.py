"""Discord UI for captured reels — link detection, spot embeds, and vote buttons.

Kept separate from bot.py so the message-handling stays lean. The buttons are
discord.py DynamicItems: the catalog event id rides in each button's custom_id,
so they keep working after a bot restart (registered via register_dynamic_items).

Talks to the same HTTP endpoints the website uses, so a Discord vote and a web
vote land in one shared store:
  ADMIN_URL      /api/events/{id}/vote   (👍/👎)   ·   /api/events/{id}  (remove)
  ADMIN_URL      /api/events                       (Add-suggestions lookup)
  RECOMMEND_URL  /recommend                        (Add-suggestions query)
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import discord
import httpx

# The admin app (ingest + votes) runs on the host; the bot reaches it via the
# host gateway in Docker, or localhost when run directly. RECOMMEND is the
# in-network service the bot already calls for /events.
ADMIN_URL = os.getenv("ADMIN_URL", os.getenv("INGEST_URL", "http://host.docker.internal:8010"))
RECOMMEND_URL = os.getenv("RECOMMEND_URL", "http://recommend:8003")

# When this many people tap "Want to go", the bot offers to lock the outing in
# as a Discord Scheduled Event.
QUORUM = int(os.getenv("SPOT_QUORUM", "3"))
# Fallback timezone for the default start time when a spot has no schedule.
BOT_TZ = os.getenv("BOT_TZ", "America/Los_Angeles")

def guild_key(source) -> str:
    """Catalog tenant key: the server's guild id, or a per-user stash in DMs.

    Works for both Messages (.author) and Interactions (.user); "" falls back to
    the legacy single-tenant catalog.
    """
    guild = getattr(source, "guild", None)
    if guild is not None:
        return str(guild.id)
    user = getattr(source, "user", None) or getattr(source, "author", None)
    return f"dm-{user.id}" if user is not None else ""


# Mirrors src/ingestion/serving/discord_format.py::_CATEGORY_EMOJI.
CATEGORY_EMOJI = {
    "food_drink": "🍽️",
    "cafe_dessert": "🍰",
    "nightlife": "🍸",
    "live_music": "🎶",
    "market_popup": "🛍️",
    "outdoors": "🏞️",
    "community": "🤝",
    "other": "📍",
}
# Embed accent per category (Imagine palette mid-tones).
_CATEGORY_COLOR = {
    "food_drink": 0xD85A30,
    "cafe_dessert": 0xD4537E,
    "nightlife": 0x7F77DD,
    "live_music": 0x378ADD,
    "market_popup": 0xEF9F27,
    "outdoors": 0x1D9E75,
    "community": 0x639922,
    "other": 0x888780,
}

# Public IG post/reel links. The shortcode charset stops at "/" or "?", so any
# ?igsh=… tracking suffix is dropped automatically.
_IG_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reels?|p|tv)/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


def emoji_for(category: str) -> str:
    return CATEGORY_EMOJI.get(category, "📍")


def extract_ig_urls(text: str) -> list[str]:
    """Return de-duplicated, normalized Instagram post/reel URLs found in text."""
    seen: set[str] = set()
    out: list[str] = []
    for match in _IG_URL_RE.finditer(text or ""):
        url = match.group(0).rstrip("/")
        key = url.lower()
        if key not in seen:
            seen.add(key)
            out.append(url + "/")  # canonical trailing slash
    return out


def build_spot_embed(event: dict) -> discord.Embed:
    """Render one catalog event as a Discord embed card."""
    category = event.get("category", "other")
    embed = discord.Embed(
        title=f"{emoji_for(category)} {event.get('venue') or 'Unknown spot'}",
        color=_CATEGORY_COLOR.get(category, 0x888780),
        url=event.get("source_url") or None,
    )
    desc_parts: list[str] = []
    blurb = event.get("blurb")
    if blurb is not None:                       # capture flow: video-based quick description
        desc_parts.append(f"📝 {blurb}" if blurb else "📝 No info")
    elif event.get("theme"):
        desc_parts.append(str(event["theme"])[:300])
    if event.get("source_url"):
        desc_parts.append(f"▶ [Watch the reel]({event['source_url']})")
    if desc_parts:
        embed.description = "\n\n".join(desc_parts)
    if event.get("image"):
        embed.set_thumbnail(url=event["image"])
    embed.add_field(name="Category", value=category.replace("_", " "), inline=True)

    start_epoch = event.get("start_epoch")
    if start_epoch:
        when = f"<t:{int(start_epoch)}:F>"
        if event.get("end_epoch"):
            when += f" – <t:{int(event['end_epoch'])}:t>"
    else:
        when = "no fixed date"
    embed.add_field(name="When", value=when, inline=True)

    lat, lng = event.get("lat"), event.get("lng")
    if lat is not None and lng is not None:
        # OpenStreetMap, matching the repo's no-paid-APIs ethos (a plain link
        # either way — no API key — but OSM keeps us off Google entirely).
        embed.add_field(
            name="Where",
            value=f"[Open map](https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=17/{lat}/{lng})",
            inline=True,
        )

    if event.get("already"):
        embed.add_field(
            name="​",
            value=f"📌 Already in the catalog · {int(event.get('votes', 0))} 👍",
            inline=False,
        )

    footer = "via Instagram"
    if event.get("sharer"):
        footer = f"shared by {event['sharer']} · {footer}"
    embed.set_footer(text=footer)
    return embed


# ── persistent buttons (event id carried in custom_id) ───────────────────────


class VoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"spot:vote:(?P<delta>-?\d+):(?P<eid>[^:]+)"):
    def __init__(self, event_id: str, delta: int, votes: int = 0):
        self.event_id = event_id
        self.delta = delta
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.primary if delta > 0 else discord.ButtonStyle.secondary,
                label=(f"Want to go · {votes}" if delta > 0 else "Not for me"),
                emoji="👍" if delta > 0 else "👎",
                custom_id=f"spot:vote:{delta}:{event_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["eid"], int(match["delta"]))

    async def callback(self, interaction: discord.Interaction):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{ADMIN_URL}/api/events/{self.event_id}/vote",
                    params={"guild_id": guild_key(interaction)},
                    json={
                        "delta": self.delta,
                        "user_id": str(interaction.user.id),
                        "user_name": interaction.user.display_name,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                votes = int(data.get("votes", 0))
                voters = data.get("voters", [])
        except Exception:
            await interaction.response.send_message(
                "⚠️ Couldn't record that vote — try again in a moment.", ephemeral=True
            )
            return

        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        await interaction.response.edit_message(
            embed=with_whos_in(embed, voters) if embed else None,
            view=build_spot_view(self.event_id, votes),
        )

        # Quorum: enough people are in — offer to make it a real Discord event.
        if self.delta > 0 and votes == QUORUM and interaction.guild is not None:
            ev = (await _events_by_id(guild_key(interaction))).get(self.event_id)
            venue = (ev or {}).get("venue") or "this spot"
            view = discord.ui.View(timeout=None)
            view.add_item(LockInButton(self.event_id))
            await interaction.followup.send(
                f"🎉 **{votes} people want {venue}** — lock it in as a server event?",
                view=view,
            )


def with_whos_in(embed: discord.Embed, voters: list[str]) -> discord.Embed:
    """Refresh the card's "Who's in" field to the current voter names."""
    for i, field in enumerate(embed.fields):
        if field.name == "Who's in":
            embed.remove_field(i)
            break
    if voters:
        embed.add_field(name="Who's in", value=", ".join(voters[:12]), inline=False)
    return embed


def default_start_time() -> datetime:
    """Next Friday 7pm (BOT_TZ) — the fallback when a spot has no schedule."""
    now_local = datetime.now(ZoneInfo(BOT_TZ))
    days_ahead = (4 - now_local.weekday()) % 7 or 7   # 4 = Friday; today → next week
    return (now_local + timedelta(days=days_ahead)).replace(
        hour=19, minute=0, second=0, microsecond=0
    )


class LockInButton(discord.ui.DynamicItem[discord.ui.Button], template=r"spot:lockin:(?P<eid>[^:]+)"):
    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.success,
                label="Lock it in",
                emoji="📅",
                custom_id=f"spot:lockin:{event_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["eid"])

    async def callback(self, interaction: discord.Interaction):
        """Create a Discord Scheduled Event for this spot (quorum reached)."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "Scheduled events only exist in servers — share the spot there to lock it in.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(thinking=True)

        ev = (await _events_by_id(guild_key(interaction))).get(self.event_id)
        if not ev:
            await interaction.followup.send("⚠️ I can't find that spot anymore.")
            return

        now = datetime.now(timezone.utc)
        start_epoch = ev.get("start_epoch")
        start = (
            datetime.fromtimestamp(start_epoch, tz=timezone.utc)
            if start_epoch
            else default_start_time()
        )
        if start <= now:                       # past schedule → still make it plannable
            start = default_start_time()
        end_epoch = ev.get("end_epoch")
        end = (
            datetime.fromtimestamp(end_epoch, tz=timezone.utc)
            if end_epoch and end_epoch > start.timestamp()
            else start + timedelta(hours=2)    # Discord requires an end for external events
        )

        lat, lng = ev.get("lat"), ev.get("lng")
        location = (
            f"https://www.openstreetmap.org/?mlat={lat}&mlon={lng}#map=17/{lat}/{lng}"
            if lat is not None and lng is not None
            else (ev.get("venue") or "TBD")[:100]
        )

        try:
            devent = await guild.create_scheduled_event(
                name=(ev.get("venue") or "Outing")[:100],
                description=(ev.get("blurb") or ev.get("theme") or "")[:1000],
                start_time=start,
                end_time=end,
                entity_type=discord.EntityType.external,
                privacy_level=discord.PrivacyLevel.guild_only,
                location=location,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ I need the **Manage Events** permission to schedule this."
            )
            return
        except Exception as exc:
            await interaction.followup.send(f"⚠️ Couldn't create the event: {exc}")
            return

        await interaction.followup.send(
            f"📅 **Locked in!** {devent.name} — <t:{int(start.timestamp())}:F>\n{devent.url}"
        )


class RetryButton(discord.ui.DynamicItem[discord.ui.Button], template=r"spot:retry:(?P<url>.+)"):
    """Re-run a failed capture — transient IG walls often clear on a second try."""

    def __init__(self, url: str):
        self.url = url
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label="Retry",
                emoji="🔁",
                custom_id=f"spot:retry:{url}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["url"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{ADMIN_URL}/api/ingest",
                    json={"urls": [self.url], "guild_id": guild_key(interaction)},
                )
                resp.raise_for_status()
                events = resp.json().get("events", [])
        except Exception:
            await interaction.followup.send("⚠️ Still couldn't reach the catalog service.")
            return
        if not events:
            await interaction.followup.send(
                "🚫 Still unreadable — it may be private. Add it yourself instead:",
                view=build_failure_view(None),
            )
            return
        ev = events[0]
        await interaction.followup.send(
            embed=build_spot_embed(ev),
            view=build_spot_view(ev["id"], int(ev.get("votes", 0))),
        )


class SpotModal(discord.ui.Modal, title="Add a spot"):
    """Manual entry — so a reel the pipeline can't read never wastes the paste."""

    venue = discord.ui.TextInput(label="Place name", max_length=100)
    vibe = discord.ui.TextInput(
        label="What's the vibe?",
        style=discord.TextStyle.paragraph,
        placeholder="late-night birria tacos, cash only, open til 2am",
        min_length=3,
        max_length=280,
    )
    link = discord.ui.TextInput(label="Link (optional)", required=False, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{ADMIN_URL}/api/manual",
                    json={
                        "venue": str(self.venue),
                        "theme": str(self.vibe),
                        "source_url": str(self.link).strip(),
                        "guild_id": guild_key(interaction),
                    },
                )
                resp.raise_for_status()
                ev = resp.json()
        except Exception:
            await interaction.followup.send("⚠️ Couldn't save that — try again in a moment.")
            return
        ev["sharer"] = interaction.user.display_name
        await interaction.followup.send(
            embed=build_spot_embed(ev),
            view=build_spot_view(ev["id"], int(ev.get("votes", 0))),
        )


class ManualAddButton(discord.ui.DynamicItem[discord.ui.Button], template=r"spot:manual:0"):
    def __init__(self):
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="Add manually",
                emoji="✍️",
                custom_id="spot:manual:0",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SpotModal())


def build_failure_view(url: Optional[str]) -> discord.ui.View:
    """Attached to every failed capture: Retry (single-link pastes) + manual add."""
    view = discord.ui.View(timeout=None)
    if url and len(f"spot:retry:{url}") <= 100:   # custom_id hard limit
        view.add_item(RetryButton(url))
    view.add_item(ManualAddButton())
    return view


class AddSuggestionsButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"spot:suggest:(?P<eid>[^:]+)"
):
    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.success,
                label="Add suggestions",
                emoji="✨",
                custom_id=f"spot:suggest:{event_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["eid"])

    async def callback(self, interaction: discord.Interaction):
        """Find spots similar to this one and post each as its own votable card."""
        await interaction.response.defer(thinking=True)
        guild = guild_key(interaction)
        query = await _event_query(self.event_id, guild)
        if not query:
            await interaction.followup.send("⚠️ I can't find that spot anymore.")
            return
        suggestions = await _suggest_events(
            interaction.channel_id, query, guild, exclude_id=self.event_id
        )
        if not suggestions:
            await interaction.followup.send(
                "No similar spots yet — paste a few more reels and try again!"
            )
            return
        await interaction.followup.send(
            f"✨ Added {len(suggestions)} similar spot"
            f"{'' if len(suggestions) == 1 else 's'} to vote on:"
        )
        for ev in suggestions:
            await interaction.followup.send(
                embed=build_spot_embed(ev),
                view=build_spot_view(ev["id"], ev.get("votes", 0)),
            )


class RemoveButton(discord.ui.DynamicItem[discord.ui.Button], template=r"spot:remove:(?P<eid>[^:]+)"):
    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.danger,
                label="Remove",
                emoji="🗑️",
                custom_id=f"spot:remove:{event_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["eid"])

    async def callback(self, interaction: discord.Interaction):
        # In a server, only members who can manage messages may remove a shared spot.
        if interaction.guild is not None and not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                "Only members who can manage messages may remove a spot.", ephemeral=True
            )
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{ADMIN_URL}/api/events/{self.event_id}",
                    params={"guild_id": guild_key(interaction)},
                )
                resp.raise_for_status()
        except Exception:
            await interaction.response.send_message(
                "⚠️ Couldn't remove that spot — try again.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="🗑️ Removed from the catalog.", embed=None, view=None)


def build_spot_view(event_id: str, votes: int = 0) -> discord.ui.View:
    """The action row attached to a spot card: vote up/down, add suggestions, remove."""
    view = discord.ui.View(timeout=None)
    view.add_item(VoteButton(event_id, 1, votes))
    view.add_item(VoteButton(event_id, -1))
    view.add_item(AddSuggestionsButton(event_id))
    view.add_item(RemoveButton(event_id))
    return view


def register_dynamic_items(client: discord.Client) -> None:
    """Call once in on_ready so buttons keep working after a restart."""
    client.add_dynamic_items(
        VoteButton, AddSuggestionsButton, RemoveButton, LockInButton,
        RetryButton, ManualAddButton,
    )


# ── helpers for the Add-suggestions button ───────────────────────────────────


async def _events_by_id(guild: str = "") -> dict:
    """Snapshot the guild's catalog as {event_id: event_dict} for enriching suggestions."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{ADMIN_URL}/api/events", params={"guild_id": guild})
            resp.raise_for_status()
            return {e["id"]: e for e in resp.json().get("events", []) if e.get("id")}
    except Exception:
        return {}


async def _event_query(event_id: str, guild: str = "") -> str:
    """Look an event up in the catalog and build a vibe query (venue + theme)."""
    ev = (await _events_by_id(guild)).get(event_id)
    if not ev:
        return ""
    return " ".join(p for p in (ev.get("venue"), ev.get("theme")) if p).strip()


async def _suggest_events(channel_id, message: str, guild: str = "", *, exclude_id: str = "", limit: int = 3) -> list[dict]:
    """Query similar spots and return them as catalog event dicts ready for cards.

    The recommender surfaces spots already in the catalog (their Chroma id is the
    content hash), so each suggestion reuses the live event — votes and all — and
    its buttons work immediately. The source card is excluded from its own results.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{RECOMMEND_URL}/recommend",
                json={
                    "channel_id": str(channel_id),
                    "message": message,
                    "mode": "command",
                    "guild_id": guild,
                },
            )
            resp.raise_for_status()
            recs = resp.json().get("recommendations", [])
    except Exception:
        return []

    catalog = await _events_by_id(guild)
    out: list[dict] = []
    seen: set[str] = {exclude_id}
    for rec in recs:
        cid = rec.get("content_hash")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        ev = catalog.get(cid) or {
            "id": cid,
            "venue": rec.get("venue_name"),
            "category": rec.get("category"),
            "theme": rec.get("core_theme"),
            "source_url": rec.get("source_url"),
            "votes": 0,
        }
        if not ev.get("blurb"):          # empty blurb → let the embed fall back to theme
            ev.pop("blurb", None)
        out.append(ev)
        if len(out) >= limit:
            break
    return out
