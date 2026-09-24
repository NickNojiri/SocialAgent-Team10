"""/feedback and /survey (Track C #20).

/feedback: a bug report or an idea, typed into a form — never scraped from chat.
/survey: the 10-statement System Usability Scale (Brooke, 1996), one statement
at a time, one tap per answer, for usability-study participants. The server
stores the answers and a participant code, not who tapped them, and the
participant isn't shown a score (so it can't color the session).
"""

from typing import Optional

import discord
import httpx

import cards
from tenant_auth import tenant_headers

# The standard SUS statements, with "the system" read as SpotBot.
SUS_STATEMENTS = (
    "I think that I would like to use SpotBot frequently.",
    "I found SpotBot unnecessarily complex.",
    "I thought SpotBot was easy to use.",
    "I think that I would need the support of a technical person to be able to use SpotBot.",
    "I found the various functions in SpotBot were well integrated.",
    "I thought there was too much inconsistency in SpotBot.",
    "I would imagine that most people would learn to use SpotBot very quickly.",
    "I found SpotBot very cumbersome to use.",
    "I felt very confident using SpotBot.",
    "I needed to learn a lot of things before I could get going with SpotBot.",
)
SCALE = "1 = strongly disagree · 3 = neutral · 5 = strongly agree"


async def _post(path: str, guild_id: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{cards.ADMIN_URL}{path}", json={"guild_id": guild_id, **body},
            headers=tenant_headers(guild_id),
        )
        resp.raise_for_status()
        return resp.json()


async def send_feedback(guild_id: str, kind: str, text: str) -> None:
    await _post("/api/feedback", guild_id, {"kind": kind, "text": text})


async def send_survey(guild_id: str, answers: list[int], participant: str) -> float:
    return (await _post("/api/survey", guild_id,
                        {"answers": answers, "participant": participant}))["score"]


class FeedbackModal(discord.ui.Modal):
    def __init__(self, guild_id: str, kind: str):
        super().__init__(title="Report a bug" if kind == "bug" else "Share an idea")
        self.guild_id = guild_id
        self.kind = kind
        self.text = discord.ui.TextInput(
            label="What happened?" if kind == "bug" else "What would make SpotBot better?",
            style=discord.TextStyle.paragraph,
            placeholder=("What you did, what you expected, what happened instead"
                         if kind == "bug" else "Anything goes"),
            max_length=1000,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await send_feedback(self.guild_id, self.kind, str(self.text.value))
        except Exception:
            await interaction.response.send_message(
                "⚠️ Couldn't send that just now — please try again in a moment.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "🙏 Thanks — sent to the team. It's saved without your name.", ephemeral=True
        )


def survey_embed(step: int, answers: list[int]) -> discord.Embed:
    embed = discord.Embed(
        title=f"Statement {step + 1} of {len(SUS_STATEMENTS)}",
        description=f"**{SUS_STATEMENTS[step]}**\n\n{SCALE}",
        color=0x6EA8FE,
    )
    embed.set_footer(text="Tap how much you agree. Your answers are saved without your name.")
    return embed


class SurveyView(discord.ui.View):
    """One statement per screen, five buttons, a Back button to fix a slip."""

    def __init__(self, guild_id: str, participant: str = "", *, timeout: float = 900):
        super().__init__(timeout=timeout)
        self.guild_id = guild_id
        self.participant = participant
        self.answers: list[int] = []
        self.message: Optional[discord.Message] = None
        for value in range(1, 6):
            button = discord.ui.Button(label=str(value), style=discord.ButtonStyle.primary, row=0)
            button.callback = self._answer_with(value)
            self.add_item(button)
        self.back = discord.ui.Button(label="Back", emoji="◀", style=discord.ButtonStyle.secondary,
                                      row=1, disabled=True)
        self.back.callback = self._go_back
        self.add_item(self.back)

    def embed(self) -> discord.Embed:
        return survey_embed(len(self.answers), self.answers)

    def _answer_with(self, value: int):
        async def callback(interaction: discord.Interaction):
            await self.answer(interaction, value)
        return callback

    async def answer(self, interaction: discord.Interaction, value: int) -> None:
        self.answers.append(value)
        if len(self.answers) < len(SUS_STATEMENTS):
            self.back.disabled = False
            await interaction.response.edit_message(embed=self.embed(), view=self)
            return
        try:
            await send_survey(self.guild_id, self.answers, self.participant)
        except Exception:
            self.answers.pop()                     # let them tap the last one again
            await interaction.response.edit_message(
                content="⚠️ Couldn't save your answers — tap your last answer again.",
                embed=self.embed(), view=self,
            )
            return
        self.stop()
        await interaction.response.edit_message(
            content=None, view=None,
            embed=discord.Embed(title="✅ Thank you!", color=0x3FB950,
                                description="Your answers are saved. You can close this."),
        )

    async def _go_back(self, interaction: discord.Interaction) -> None:
        if self.answers:
            self.answers.pop()
        self.back.disabled = not self.answers
        await interaction.response.edit_message(content=None, embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="This survey timed out — run `/survey` to start again.",
                                        view=self)
            except Exception:
                pass
