"""Render recommendations as Discord markdown (Phase 6).

Reuses the bot's existing Discord timestamp style (`<t:epoch:F>` in
app/bot.py::handle_create_event) so event times localize per viewer.
"""

from src.ingestion.serving.recommender import Recommendation

# One emoji per EventCategory value (see schemas/inspiration.py).
_CATEGORY_EMOJI = {
    "food_drink": "🍽️",
    "cafe_dessert": "🍰",
    "nightlife": "🍸",
    "live_music": "🎶",
    "market_popup": "🛍️",
    "outdoors": "🏞️",
    "community": "🤝",
    "other": "📍",
}

_EMPTY = "I couldn't find any matching event inspirations yet — try a different vibe!"


def _time_line(rec: Recommendation) -> str:
    if rec.start_epoch:
        if rec.end_epoch:
            return f"🗓️ <t:{rec.start_epoch}:F> – <t:{rec.end_epoch}:t>"
        return f"🗓️ <t:{rec.start_epoch}:F>"
    return "🗓️ time TBD"


def format_recommendation(rec: Recommendation) -> str:
    emoji = _CATEGORY_EMOJI.get(rec.category, "📍")
    category = rec.category.replace("_", " ")
    lines = [f"**{emoji} {rec.venue_name}** — *{category}*"]
    if rec.core_theme:
        lines.append(rec.core_theme)
    lines.append(_time_line(rec))
    if rec.source_url:
        lines.append(f"🔗 <{rec.source_url}>")
    return "\n".join(lines)


def format_recommendations(recs: list[Recommendation]) -> str:
    if not recs:
        return _EMPTY
    header = "✨ **Here's what I found:**"
    return header + "\n\n" + "\n\n".join(format_recommendation(r) for r in recs)
