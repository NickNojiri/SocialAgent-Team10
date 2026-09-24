"""/privacy (Track C #21): what SpotBot keeps, and deleting all of it.

Everyone gets the plain-language explanation. Someone with Manage Server (or
anyone, for their own DM stash) also gets a button that deletes the data. It is
two steps on purpose: the button opens a form where the server's exact name has
to be typed, and the form says the deletion can't be undone.
"""

from typing import Awaitable, Callable, Optional

import discord
import httpx

import cards
from tenant_auth import tenant_headers

DM_CONFIRM_WORD = "delete"


class ForgetBusy(RuntimeError):
    """A capture for this server is still running; the API refused (409)."""


async def forget_data(guild_id: str) -> dict:
    """POST /api/forget. `confirm` repeats the id: the API's guard against a stray call."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{cards.ADMIN_URL}/api/forget",
            json={"guild_id": guild_id, "confirm": guild_id},
            headers=tenant_headers(guild_id),
        )
        if resp.status_code == 409:
            raise ForgetBusy("a capture is still running")
        resp.raise_for_status()
        return resp.json()


def privacy_embed(*, in_server: bool, can_delete: bool) -> discord.Embed:
    where = "this server" if in_server else "your DM stash"
    embed = discord.Embed(title=f"🔒 What SpotBot keeps about {where}", color=0x6EA8FE)
    embed.add_field(name="What's saved", inline=False, value=(
        "• **Spots** from links you paste: the link, place name, category, vibe, a short "
        "summary, the post's picture link, and map coordinates when found\n"
        "• **Votes** and **\"we went\" check-ins**, with the display names of who voted\n"
        "• **`/setup` settings**: the reels channel and home city\n"
        "• **Capture history**: which links were captured, how long it took, and why any failed\n"
        "• **Copies of pages that failed to load**, to work out why"
    ))
    embed.add_field(name="Not saved", inline=False, value=(
        "Your chat. `/plan` reads the last 25 messages to make a plan and doesn't keep them. "
        "Reel audio is turned into text and the audio file is deleted straight away."
    ))
    embed.add_field(name="Where it goes", inline=False, value=(
        "It stays on the computer running SpotBot. Captions and audio are read by AI models "
        "running on that computer — nothing goes to paid AI services. Place names you "
        "mention to `/plan` or `/events` may be looked up on OpenStreetMap to rank by distance."
    ))
    embed.add_field(name="Who can see it", inline=False, value=(
        "People in this server, anyone you send a `/share` link to, and whoever runs SpotBot."
        if in_server else "You, and whoever runs SpotBot."
    ))
    embed.add_field(name="How long", inline=False, value=(
        "Until it's deleted. "
        + ("You can delete all of it with the button below." if can_delete
           else "Someone with Manage Server can delete all of it from `/privacy`.")
    ))
    return embed


def deleted_embed(report: dict) -> discord.Embed:
    catalog = report.get("catalog", {})
    spots = int(catalog.get("spots", 0))
    lines = [
        f"🗑️ **{spots} spot{'s' if spots != 1 else ''}**, with their votes and check-ins",
        "⚙️ `/setup` settings" if report.get("settings") else None,
        "🕓 capture history and failed-page copies",
    ]
    kept = int(catalog.get("shared_posts_kept", 0))
    note = (f"\n-# {kept} of those posts were also saved by another server, so their copy "
            "stays in that server's catalog." if kept else "")
    return discord.Embed(
        title="Deleted — this can't be undone",
        color=0xD29922,
        description="\n".join(line for line in lines if line) + note
        + "\n\nPaste a reel any time to start a fresh catalog.",
    )


class ConfirmDeleteModal(discord.ui.Modal, title="Delete this server's SpotBot data"):
    """Step 2: type the server's exact name (or 'delete' in a DM)."""

    def __init__(self, guild_id: str, expected: str, on_deleted: Callable[[], Awaitable[None]]):
        super().__init__(title="Delete your saved SpotBot data" if expected == DM_CONFIRM_WORD
                         else "Delete this server's SpotBot data")
        self.guild_id = guild_id
        self.expected = expected
        self.on_deleted = on_deleted
        self.answer = discord.ui.TextInput(
            label="Type the server name to confirm" if expected != DM_CONFIRM_WORD
            else "Type delete to confirm",
            placeholder=expected[:100],
            max_length=100,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        if str(self.answer.value).strip() != self.expected:
            await interaction.response.send_message(
                "That didn't match, so nothing was deleted.", ephemeral=True
            )
            return
        try:
            report = await forget_data(self.guild_id)
        except ForgetBusy:
            await interaction.response.send_message(
                "⏳ A reel is still being captured here. Nothing was deleted — try again "
                "in a minute.", ephemeral=True
            )
            return
        except Exception:
            await interaction.response.send_message(
                "⚠️ Couldn't reach the catalog, so nothing was deleted. Try again shortly.",
                ephemeral=True,
            )
            return
        await self.on_deleted()
        await interaction.response.send_message(embed=deleted_embed(report), ephemeral=True)


class PrivacyView(discord.ui.View):
    """Step 1: the button, under the explanation. Transient, like BrowseView."""

    def __init__(self, guild_id: str, expected: str, on_deleted: Callable[[], Awaitable[None]],
                 *, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.expected = expected
        self.on_deleted = on_deleted
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="Delete all of this data…", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_modal(
            ConfirmDeleteModal(self.guild_id, self.expected, self.on_deleted)
        )

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
