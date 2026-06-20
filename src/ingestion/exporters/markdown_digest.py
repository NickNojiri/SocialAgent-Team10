"""Markdown digest exporter for validated EventInspiration records."""

from collections import defaultdict
from pathlib import Path
import sys

from src.ingestion.schemas.inspiration import (
    EventCategory,
    EventInspiration,
    ScheduleStatus,
)

_CATEGORY_EMOJI = {
    EventCategory.FOOD_DRINK: "🍽️",
    EventCategory.CAFE_DESSERT: "☕",
    EventCategory.NIGHTLIFE: "🌙",
    EventCategory.LIVE_MUSIC: "🎵",
    EventCategory.MARKET_POPUP: "🛍️",
    EventCategory.OUTDOORS: "🌿",
    EventCategory.COMMUNITY: "🤝",
    EventCategory.OTHER: "✨",
}


def render_digest(records: list[EventInspiration]) -> str:
    if not records:
        return "_No event inspirations yet._"

    grouped: dict[EventCategory, list[EventInspiration]] = defaultdict(list)
    for record in records:
        grouped[record.category].append(record)

    sections: list[str] = []
    for category in EventCategory:
        category_records = grouped.get(category, [])
        if not category_records:
            continue

        title = category.value.replace("_", " ").title()
        lines = [f"## {_CATEGORY_EMOJI[category]} {title}"]
        for record in _scheduled_first(category_records):
            lines.extend(_event_block(record))
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def digest_from_jsonl(path: str | Path) -> str:
    jsonl_path = Path(path)
    if not jsonl_path.exists():
        return render_digest([])

    records = [
        EventInspiration.model_validate_json(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return render_digest(records)


def _scheduled_first(records: list[EventInspiration]) -> list[EventInspiration]:
    scheduled = [record for record in records if _is_scheduled(record)]
    unscheduled = [record for record in records if not _is_scheduled(record)]
    return sorted(scheduled, key=lambda record: record.schedule.start_utc) + unscheduled


def _is_scheduled(record: EventInspiration) -> bool:
    return (
        record.schedule is not None
        and record.schedule.status is ScheduleStatus.SCHEDULED
        and record.schedule.start_utc is not None
    )


def _event_block(record: EventInspiration) -> list[str]:
    return [
        f"- **{record.venue_name}** — {record.core_theme}",
        f"  - 📅 {_schedule_text(record)}",
        f"  - 📍 {_location_text(record)}",
        f"  - 🔗 {record.provenance.source_url}",
    ]


def _schedule_text(record: EventInspiration) -> str:
    if _is_scheduled(record):
        return f"<t:{int(record.schedule.start_utc.timestamp())}:F>"
    return "TBD"


def _location_text(record: EventInspiration) -> str:
    geo = record.geo
    if geo.place_names:
        return ", ".join(geo.place_names)
    if geo.lat is not None:
        return f"{geo.lat:.4f},{geo.lng:.4f}"
    return "—"


if __name__ == "__main__":
    print(digest_from_jsonl(sys.argv[1] if len(sys.argv) > 1 else "data/inspirations.jsonl"))
