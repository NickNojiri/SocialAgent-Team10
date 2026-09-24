"""The /setup wizard (Track C #8): pick the reels channel, set the home city, save.

Settings are per server and live in the admin app (GET/PUT /api/settings), not
in this process, so they survive a bot restart and a redeploy. The wizard
itself is a transient View like BrowseView: if it times out, run /setup again.

A server that never ran /setup behaves exactly as before: capture everywhere
the bot can read. If the admin app can't be reached, capture does the same
rather than silently dropping pastes.
"""

import time
from typing import Optional

import discord
import httpx

import cards
from tenant_auth import tenant_headers

CACHE_TTL_S = 300.0
_CACHE: dict[str, tuple[float, dict]] = {}   # guild id → (fetched at, settings)


# ── transport ───────────────────────────────────────────────────────────────

async def fetch_settings(guild_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{cards.ADMIN_URL}/api/settings",
            params={"guild_id": guild_id},
            headers=tenant_headers(guild_id),
        )
        resp.raise_for_status()
        return resp.json()["settings"]


async def save_settings(guild_id: str, changes: dict) -> dict:
    """Send only what changed; the API keeps everything else as it was."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.put(
            f"{cards.ADMIN_URL}/api/settings",
            json={"guild_id": guild_id, **changes},
            headers=tenant_headers(guild_id),
        )
        resp.raise_for_status()
        saved = resp.json()["settings"]
    _CACHE[guild_id] = (time.monotonic(), saved)
    return saved


async def settings_for(guild_id: str, *, fresh: bool = False) -> Optional[dict]:
    """This server's settings, cached for a few minutes; None if unreachable."""
    hit = _CACHE.get(guild_id)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_TTL_S:
        return hit[1]
    try:
        settings = await fetch_settings(guild_id)
    except Exception:
        return None
    _CACHE[guild_id] = (time.monotonic(), settings)
    return settings


async def captures_here(message) -> bool:
    """Should a link pasted in this message's channel be captured?"""
    if message.guild is None:
        return True                                  # DMs always capture
    settings = await settings_for(str(message.guild.id))
    drop = (settings or {}).get("drop_channel_id")
    if not drop:
        return True                                  # never set up, or unreachable
    channel = message.channel
    # A thread inside the drop channel counts as the drop channel.
    return str(channel.id) == drop or str(getattr(channel, "parent_id", None) or "") == drop


# ── text ────────────────────────────────────────────────────────────────────

def channel_text(channel_id: Optional[str]) -> str:
    return f"<#{channel_id}>" if channel_id else "every channel I can read"


def city_text(city: str) -> str:
    return city or "not set"


def overview_embed(settings: Optional[dict], *, can_manage: bool, here: str) -> discord.Embed:
    """/setup for everyone: how it works, and how this server is set up."""
    embed = discord.Embed(
        title="🧭 SpotBot in 20 seconds",
        color=0x6EA8FE,
        description=(
            "**1. Paste** an Instagram or TikTok reel link — I turn it into a votable spot card.\n"
            f"**2. Vote** with 👍 — at **{cards.QUORUM}** I offer to put it on the server calendar.\n"
            "**3. Plan** with `/plan` — I read the recent chat and pitch saved spots that match."
        ),
    )
    if settings is None:
        embed.add_field(name="This server", value="⚠️ Couldn't load the settings right now.",
                        inline=False)
    else:
        embed.add_field(name="Reels channel", value=channel_text(settings.get("drop_channel_id")),
                        inline=True)
        embed.add_field(name="Home city", value=city_text(settings.get("home_city", "")),
                        inline=True)
    embed.add_field(name="This channel", value=here, inline=False)
    if not can_manage:
        embed.set_footer(text="Someone with Manage Server can run /setup to pick the reels "
                              "channel and home city.")
    return embed


def wizard_embed(current: dict, pending: dict, note: str = "") -> discord.Embed:
    """The wizard's one message: each step with what it will save."""
    def line(step: str, label: str, key: str, render) -> str:
        value = pending[key] if key in pending else current.get(key)
        mark = " ✏️" if key in pending and pending[key] != current.get(key) else ""
        return f"**{step}. {label}** — {render(value)}{mark}"

    lines = [
        line("1", "Reels channel", "drop_channel_id", channel_text),
        line("2", "Home city", "home_city", lambda v: city_text(v or "")),
        "**3. Save** — nothing else changes: saved spots, votes and plans stay put.",
    ]
    if note:
        lines.append(f"\n{note}")
    embed = discord.Embed(title="🧭 Set up SpotBot — about 30 seconds", color=0x6EA8FE,
                          description="\n".join(lines))
    embed.set_footer(text="Only you can see this. ✏️ = will change when you save.")
    return embed


def done_embed(saved: dict) -> discord.Embed:
    where = channel_text(saved.get("drop_channel_id"))
    return discord.Embed(
        title="✅ SpotBot is set up",
        color=0x3FB950,
        description=(
            f"Reels channel: {where} · Home city: {city_text(saved.get('home_city', ''))}\n\n"
            f"Paste an Instagram or TikTok reel link in {where} to see your first card. "
            "Run `/setup` again any time — it only changes what you change."
        ),
    )


def join_embed() -> discord.Embed:
    """Posted once when the bot joins a server — the fastest path to a first card."""
    return discord.Embed(
        title="👋 Thanks for adding SpotBot",
        color=0x6EA8FE,
        description=(
            "Paste an Instagram or TikTok reel link in any channel and I'll turn it into a "
            "spot card your friends can vote on.\n\n"
            "Want reels in one channel only, or distance from your city? Someone with "
            "Manage Server can run `/setup` — it takes about 30 seconds."
        ),
    )


# ── the wizard ──────────────────────────────────────────────────────────────

class CityModal(discord.ui.Modal, title="Your group's home city"):
    def __init__(self, wizard: "SetupWizard"):
        super().__init__()
        self.wizard = wizard
        current = wizard.pending.get("home_city", wizard.current.get("home_city", ""))
        self.city = discord.ui.TextInput(
            label="Home city",
            placeholder="e.g. Long Beach, CA",
            default=current or None,
            max_length=80,
        )
        self.add_item(self.city)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.pending["home_city"] = " ".join(str(self.city.value).split())
        await interaction.response.edit_message(embed=self.wizard.embed(), view=self.wizard)


class SetupWizard(discord.ui.View):
    """Step 1 channel, step 2 city, step 3 save. Holds only the changes; nothing
    is saved until Save, and Save sends only what changed."""

    def __init__(self, guild_id: str, current: dict, *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.current = dict(current)
        self.pending: dict = {}
        self.note = ""
        self.message: Optional[discord.Message] = None

    def embed(self) -> discord.Embed:
        return wizard_embed(self.current, self.pending, self.note)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="1 · Where should I watch for reels?",
        row=0,
    )
    async def pick_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        picked = select.values[0]
        self.pending["drop_channel_id"] = str(picked.id)
        self.note = ""
        resolved = interaction.guild.get_channel(picked.id) if interaction.guild else None
        me = interaction.guild.me if interaction.guild else None
        if resolved is not None and me is not None:
            perms = resolved.permissions_for(me)
            if not (perms.view_channel and perms.send_messages):
                self.note = (f"⚠️ I can't read or post in <#{picked.id}> yet — give SpotBot "
                             "access to it, or pick another channel.")
        await self._refresh(interaction)

    @discord.ui.button(label="Every channel", style=discord.ButtonStyle.secondary, row=1)
    async def every_channel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.pending["drop_channel_id"] = None
        self.note = ""
        await self._refresh(interaction)

    @discord.ui.button(label="Create #spot-drops", emoji="➕", style=discord.ButtonStyle.secondary,
                       row=1)
    async def create_channel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            channel = await interaction.guild.create_text_channel(
                "spot-drops", reason=f"SpotBot /setup, run by {interaction.user}"
            )
        except discord.Forbidden:
            self.note = ("⚠️ I don't have Manage Channels here. Create the channel yourself, "
                         "then pick it above.")
        except discord.HTTPException:
            self.note = "⚠️ Discord didn't let me create the channel. Pick an existing one above."
        else:
            self.pending["drop_channel_id"] = str(channel.id)
            self.note = f"Created <#{channel.id}>."
        await self._refresh(interaction)

    @discord.ui.button(label="2 · Home city", emoji="📍", style=discord.ButtonStyle.primary, row=2)
    async def set_city(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(CityModal(self))

    @discord.ui.button(label="3 · Save", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: discord.Interaction, _button: discord.ui.Button):
        try:
            saved = await save_settings(self.guild_id, self.pending)
        except Exception:
            self.note = "⚠️ Couldn't save just now — nothing was changed. Try Save again."
            await self._refresh(interaction)
            return
        self.stop()
        await interaction.response.edit_message(embed=done_embed(saved), view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=2)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            content="Setup cancelled — nothing was changed.", embed=None, view=None
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
