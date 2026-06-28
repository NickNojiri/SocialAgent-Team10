"""Discord UI for captured reels — link detection, spot embeds, and vote buttons.

Kept separate from bot.py so the message-handling stays lean. The buttons are
discord.py DynamicItems: the catalog event id rides in each button's custom_id,
so they keep working after a bot restart (registered via register_dynamic_items).

Talks to the same HTTP endpoints the website uses, so a Discord vote and a web
vote land in one shared store:
  ADMIN_URL      /api/events/{id}/vote   (👍/👎)   ·   /api/events/{id}  (remove)
  ADMIN_URL      /api/events                       (Similar nearby lookup)
  RECOMMEND_URL  /recommend                        (Similar nearby query)
"""

import os
import re

import discord
import httpx

# The admin app (ingest + votes) runs on the host; the bot reaches it via the
# host gateway in Docker, or localhost when run directly. RECOMMEND is the
# in-network service the bot already calls for /events.
ADMIN_URL = os.getenv("ADMIN_URL", os.getenv("INGEST_URL", "http://host.docker.internal:8010"))
RECOMMEND_URL = os.getenv("RECOMMEND_URL", "http://recommend:8003")

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
    embed.add_field(name="Category", value=category.replace("_", " "), inline=True)

    start_epoch = event.get("start_epoch")
    if start_epoch:
        when = f"<t:{int(start_epoch)}:F>"
        if event.get("end_epoch"):
            when += f" – <t:{int(event['end_epoch'])}:t>"
    else:
        when = "no fixed date"
    embed.add_field(name="When", value=when, inline=True)

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
                    f"{ADMIN_URL}/api/events/{self.event_id}/vote", json={"delta": self.delta}
                )
                resp.raise_for_status()
                votes = int(resp.json().get("votes", 0))
        except Exception:
            await interaction.response.send_message(
                "⚠️ Couldn't record that vote — try again in a moment.", ephemeral=True
            )
            return
        await interaction.response.edit_message(view=build_spot_view(self.event_id, votes))


class SimilarButton(discord.ui.DynamicItem[discord.ui.Button], template=r"spot:similar:(?P<eid>[^:]+)"):
    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="Similar nearby",
                emoji="📍",
                custom_id=f"spot:similar:{event_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["eid"])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        query = await _event_query(self.event_id)
        if not query:
            await interaction.followup.send("⚠️ I can't find that spot anymore.")
            return
        markdown = await _recommend(interaction.channel_id, query)
        await interaction.followup.send(markdown or "No similar spots yet — paste a few more reels!")


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
                resp = await client.delete(f"{ADMIN_URL}/api/events/{self.event_id}")
                resp.raise_for_status()
        except Exception:
            await interaction.response.send_message(
                "⚠️ Couldn't remove that spot — try again.", ephemeral=True
            )
            return
        await interaction.response.edit_message(content="🗑️ Removed from the catalog.", embed=None, view=None)


def build_spot_view(event_id: str, votes: int = 0) -> discord.ui.View:
    """The action row attached to a spot card: vote up/down, similar, remove."""
    view = discord.ui.View(timeout=None)
    view.add_item(VoteButton(event_id, 1, votes))
    view.add_item(VoteButton(event_id, -1))
    view.add_item(SimilarButton(event_id))
    view.add_item(RemoveButton(event_id))
    return view


def register_dynamic_items(client: discord.Client) -> None:
    """Call once in on_ready so buttons keep working after a restart."""
    client.add_dynamic_items(VoteButton, SimilarButton, RemoveButton)


# ── helpers for the Similar button ───────────────────────────────────────────


async def _event_query(event_id: str) -> str:
    """Look an event up in the catalog and build a vibe query (venue + theme)."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{ADMIN_URL}/api/events")
            resp.raise_for_status()
            for ev in resp.json().get("events", []):
                if ev.get("id") == event_id:
                    return " ".join(p for p in (ev.get("venue"), ev.get("theme")) if p).strip()
    except Exception:
        pass
    return ""


async def _recommend(channel_id, message: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{RECOMMEND_URL}/recommend",
                json={"channel_id": str(channel_id), "message": message, "mode": "command"},
            )
            resp.raise_for_status()
            return resp.json().get("markdown", "")
    except Exception:
        return ""
