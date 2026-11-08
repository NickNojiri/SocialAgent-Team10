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

# Plain names, as app/cards.py::CATEGORY_LABEL shows them on spot cards.
_CATEGORY_LABEL = {
    "food_drink": "Food & drink", "cafe_dessert": "Cafés & dessert", "nightlife": "Nightlife",
    "live_music": "Live music", "market_popup": "Markets & pop-ups", "outdoors": "Outdoors",
    "community": "Community", "other": "Other spots",
}

_EMPTY = "I couldn't find any matching event inspirations yet — try a different vibe!"


def _label(category: str) -> str:
    return _CATEGORY_LABEL.get(category, category.replace("_", " ").capitalize())


def _time_line(rec: Recommendation) -> str | None:
    """The date, or nothing: a spot without one doesn't advertise the gap (like the spot card)."""
    if rec.start_epoch:
        if rec.end_epoch:
            return f"<t:{rec.start_epoch}:F> – <t:{rec.end_epoch}:t>"
        return f"<t:{rec.start_epoch}:F>"
    return None


def _header(n: int) -> str:
    return "1 spot that matches" if n == 1 else f"{n} spots that match"


def format_recommendation(rec: Recommendation) -> str:
    emoji = _CATEGORY_EMOJI.get(rec.category, "📍")
    prefix = "📌 " if rec.pinned else ""
    lines = [f"{prefix}**{emoji} {rec.venue_name}** · {_label(rec.category)}"]
    if rec.core_theme:
        lines.append(rec.core_theme)
    when = _time_line(rec)
    if when:
        lines.append(when)
    if rec.distance_km is not None:
        lines.append(f"{rec.distance_km:g} km away")
    if rec.source_url:
        lines.append(f"<{rec.source_url}>")
    return "\n".join(lines)


def format_recommendations(recs: list[Recommendation]) -> str:
    if not recs:
        return _EMPTY
    header = f"**{_header(len(recs))}**"
    return header + "\n\n" + "\n\n".join(format_recommendation(r) for r in recs)


def format_as_embed(recs: list[Recommendation]) -> dict:
    if not recs:
        return {"title": "No Recommendations", "description": _EMPTY, "color": 0xFF0000}
    
    fields = []
    for rec in recs:
        emoji = _CATEGORY_EMOJI.get(rec.category, "📍")
        value_lines = []
        if rec.core_theme:
            value_lines.append(rec.core_theme)
        when = _time_line(rec)
        if when:
            value_lines.append(when)
        if rec.source_url:
            value_lines.append(f"[Source link]({rec.source_url})")
            
        fields.append({
            "name": f"{emoji} {rec.venue_name} · {_label(rec.category)}",
            "value": "\n".join(value_lines) or "\u200b",
            "inline": False
        })
        
    return {
        "title": _header(len(recs)),
        "color": 0x00FF00,
        "fields": fields
    }
