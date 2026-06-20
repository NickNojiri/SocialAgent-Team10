from datetime import datetime, timezone

from src.ingestion.exporters.markdown_digest import digest_from_jsonl, render_digest
from src.ingestion.schemas.inspiration import (
    EventCategory,
    EventInspiration,
    EventSchedule,
    GeoContext,
    GeoResolution,
    GeoSource,
    ScheduleStatus,
    SourceProvenance,
    content_hash,
)


NOW = datetime(2026, 6, 20, 18, 0, tzinfo=timezone.utc)


def record(
    *,
    venue_name="Casa Loma",
    core_theme="birria tacos pop-up",
    category=EventCategory.FOOD_DRINK,
    place_names=None,
    lat=None,
    lng=None,
    schedule=None,
    source_url="https://example.com/post",
):
    return EventInspiration(
        venue_name=venue_name,
        core_theme=core_theme,
        category=category,
        geo=GeoContext(
            place_names=place_names or [],
            lat=lat,
            lng=lng,
            source=GeoSource.CAPTION_TEXT,
            confidence=0.8,
            resolution=GeoResolution.GEOCODED,
        ),
        schedule=schedule,
        provenance=SourceProvenance(
            source_url=source_url,
            platform="generic",
            fetched_at=NOW,
            content_hash=content_hash(venue_name + core_theme),
            extractor="test/0.1",
        ),
    )


def scheduled(start_utc=None):
    return EventSchedule(
        status=ScheduleStatus.SCHEDULED,
        start_utc=start_utc or datetime(2026, 6, 21, 3, 0, tzinfo=timezone.utc),
        time_known=True,
        parsed_from="Saturday 8pm",
    )


def test_render_digest_empty():
    assert render_digest([]) == "_No event inspirations yet._"


def test_scheduled_food_drink_event_includes_header_and_timestamp():
    text = render_digest(
        [
            record(
                venue_name="Casa Loma",
                category=EventCategory.FOOD_DRINK,
                place_names=["Long Beach"],
                schedule=scheduled(),
            )
        ]
    )

    assert "## " in text
    assert "Casa Loma" in text
    assert "<t:" in text


def test_unscheduled_event_uses_tbd():
    text = render_digest([record(venue_name="Moonlight Market", category=EventCategory.MARKET_POPUP)])

    assert "Moonlight Market" in text
    assert "TBD" in text


def test_digest_from_jsonl_roundtrip_and_missing_file(tmp_path):
    records = [
        record(venue_name="Casa Loma", category=EventCategory.FOOD_DRINK),
        record(venue_name="Harbor Stage", category=EventCategory.LIVE_MUSIC),
    ]
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(item.model_dump_json() for item in records) + "\n",
        encoding="utf-8",
    )

    text = digest_from_jsonl(path)

    assert "Casa Loma" in text
    assert "Harbor Stage" in text
    assert digest_from_jsonl(tmp_path / "missing.jsonl") == "_No event inspirations yet._"
