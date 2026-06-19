"""Unit + offline integration tests for the Phase 1 ingestion engine.

Run:  .venv\\Scripts\\python -m pytest test_ingestion.py -v

Offline by design: schema/extractor tests build snapshots by hand; the
Playwright tests navigate local fixture files (allow_file_urls) — no network.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from src.ingestion.config import IngestionSettings
from src.ingestion.browser.session_manager import SocialSessionManager
from src.ingestion.extractors.base import select_extractor
from src.ingestion.extractors.generic import GenericExtractor
from src.ingestion.extractors.instagram import InstagramExtractor
from src.ingestion.pipeline.geo_enricher import GeoEnricher
from src.ingestion.pipeline.llm_extractor import LlmFieldExtractor
from src.ingestion.pipeline.normalizer import build_llm_payload, normalize
from src.ingestion.pipeline.temporal_resolver import TemporalResolver
from src.ingestion.pipeline.validator import build_record
from src.ingestion.schemas.extraction import CategoryLiteral, LlmExtraction
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
from src.logic.parser import TimeParser
from src.ingestion.pipeline.orchestrator import IngestionPipeline, RunReport, result_line
from src.ingestion.schemas.results import FetchStatus
from src.ingestion.schemas.snapshot import (
    PageSnapshot,
    RawPostSnapshot,
    TextComponent,
    TextRole,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime.now(timezone.utc)

IG_URL = "https://www.instagram.com/p/TEST123/"
IG_CAPTION = (
    "Late-night birria tacos pop-up at Casa Loma 📍 4th St, Long Beach — "
    "this Friday 8pm! #birria #LongBeachFood #tacos"
)
IG_OG_TITLE = "Bites of LB (@bitesoflb) • Instagram photos and videos"
IG_OG_DESCRIPTION = (
    f'120 likes, 8 comments - bitesoflb on June 5, 2026: "{IG_CAPTION}"'
)


def make_snapshot(**overrides) -> PageSnapshot:
    defaults = dict(url=IG_URL, status=FetchStatus.OK, fetched_at=NOW)
    defaults.update(overrides)
    return PageSnapshot(**defaults)


def make_provenance(**overrides) -> SourceProvenance:
    defaults = dict(
        source_url="https://www.instagram.com/p/TEST123/",
        platform="instagram",
        fetched_at=NOW,
        content_hash=content_hash("anything"),
        extractor="instagram/0.1",
    )
    defaults.update(overrides)
    return SourceProvenance(**defaults)


def make_record_kwargs(**overrides) -> dict:
    defaults = dict(
        venue_name="Casa Loma",
        core_theme="Late-night birria tacos pop-up",
        category=EventCategory.FOOD_DRINK,
        geo=GeoContext(),
        provenance=make_provenance(),
    )
    defaults.update(overrides)
    return defaults


# ── EventInspiration schema ─────────────────────────────────────────────────


class TestEventInspirationSchema:
    def test_valid_record(self):
        record = EventInspiration(**make_record_kwargs())
        assert record.venue_name == "Casa Loma"
        assert record.record_id is not None

    def test_empty_venue_rejected(self):
        with pytest.raises(ValidationError):
            EventInspiration(**make_record_kwargs(venue_name="  "))

    def test_boilerplate_venue_rejected(self):
        with pytest.raises(ValidationError, match="boilerplate"):
            EventInspiration(**make_record_kwargs(venue_name="Instagram"))

    def test_short_theme_rejected(self):
        with pytest.raises(ValidationError):
            EventInspiration(**make_record_kwargs(core_theme="ok"))

    def test_lat_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            GeoContext(lat=123.0, lng=0.0)

    def test_hashtags_normalized_and_deduped(self):
        record = EventInspiration(
            **make_record_kwargs(),
            hashtags=["#Tacos", "tacos", "#LongBeachFood", ""],
        )
        assert record.hashtags == ["tacos", "longbeachfood"]

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            EventInspiration(**make_record_kwargs(), surprise_field="nope")


# ── Extractors ──────────────────────────────────────────────────────────────


class TestInstagramExtractor:
    def test_claims_post_and_reel_urls(self):
        extractor = InstagramExtractor()
        assert extractor.claims("https://www.instagram.com/p/ABC123/")
        assert extractor.claims("https://instagram.com/reel/XYZ/")
        assert not extractor.claims("https://www.instagram.com/someuser/")
        assert not extractor.claims("https://example.com/p/ABC123/")

    def test_extract_from_og_tags(self):
        snapshot = make_snapshot(
            meta={"og:title": IG_OG_TITLE, "og:description": IG_OG_DESCRIPTION}
        )
        raw = InstagramExtractor().extract(snapshot)
        assert raw.caption == IG_CAPTION
        assert raw.author_handle == "bitesoflb"
        assert set(raw.hashtags) >= {"birria", "LongBeachFood", "tacos"}
        assert raw.platform == "instagram"

    def test_rendered_caption_preferred_over_og_excerpt(self):
        snapshot = make_snapshot(
            meta={"og:description": IG_OG_DESCRIPTION},
            components=[TextComponent(role=TextRole.CAPTION, text="rendered caption")],
        )
        raw = InstagramExtractor().extract(snapshot)
        assert raw.caption == "rendered caption"

    def test_unparseable_og_description_kept_verbatim(self):
        snapshot = make_snapshot(meta={"og:description": "a brand new format"})
        raw = InstagramExtractor().extract(snapshot)
        assert raw.caption == "a brand new format"


class TestGenericExtractor:
    def test_jsonld_event(self):
        snapshot = make_snapshot(
            url="https://example.com/events/jazz-night",
            jsonld=[
                {
                    "@type": "Event",
                    "name": "Live Jazz Night",
                    "description": "An evening of live jazz in downtown Long Beach.",
                    "startDate": "2026-06-19T20:00:00-07:00",
                    "location": {
                        "@type": "Place",
                        "name": "The Harbor House",
                        "address": {
                            "streetAddress": "411 Shoreline Dr",
                            "addressLocality": "Long Beach",
                            "addressRegion": "CA",
                        },
                        "geo": {"latitude": 33.7626, "longitude": -118.1955},
                    },
                }
            ],
        )
        raw = GenericExtractor().extract(snapshot)
        assert raw.venue_candidate == "The Harbor House"
        assert raw.location_text == "411 Shoreline Dr, Long Beach, CA"
        assert raw.lat == pytest.approx(33.7626)
        assert raw.lng == pytest.approx(-118.1955)
        assert raw.start_date_raw == "2026-06-19T20:00:00-07:00"

    def test_select_extractor_routing(self):
        assert isinstance(select_extractor(IG_URL), InstagramExtractor)
        assert isinstance(select_extractor("https://example.com/x"), GenericExtractor)


# ── Normalizer ──────────────────────────────────────────────────────────────


def make_raw(**overrides) -> RawPostSnapshot:
    defaults = dict(
        source_url=IG_URL,
        platform="instagram",
        extractor="instagram/0.1",
        fetched_at=NOW,
    )
    defaults.update(overrides)
    return RawPostSnapshot(**defaults)


class TestNormalizer:
    def test_venue_from_at_pattern(self):
        candidates = normalize(make_raw(caption=IG_CAPTION))
        assert candidates["venue_name"] == "Casa Loma"

    def test_pin_line_becomes_caption_geo(self):
        candidates = normalize(make_raw(caption=IG_CAPTION))
        geo = candidates["geo"]
        assert geo.source is GeoSource.CAPTION_TEXT
        assert geo.raw_location_text == "4th St, Long Beach"
        assert "Long Beach" in geo.place_names

    def test_platform_location_outranks_caption(self):
        candidates = normalize(
            make_raw(caption=IG_CAPTION, location_text="Long Beach, California")
        )
        assert candidates["geo"].source is GeoSource.PLATFORM_LOCATION_TAG
        assert candidates["geo"].confidence == pytest.approx(0.9)

    def test_category_keywords(self):
        assert normalize(make_raw(caption="best matcha latte in town"))["category"] is EventCategory.CAFE_DESSERT
        assert normalize(make_raw(caption="Friday jazz concert on the pier"))["category"] is EventCategory.LIVE_MUSIC
        assert normalize(make_raw(caption=IG_CAPTION))["category"] is EventCategory.MARKET_POPUP
        assert normalize(make_raw(caption="something entirely unrelated"))["category"] is EventCategory.OTHER

    def test_time_mentions_collected(self):
        times = normalize(make_raw(caption=IG_CAPTION))["candidate_times"]
        assert any("friday" in t.lower() for t in times)
        assert any("8pm" in t.lower() for t in times)

    def test_heuristic_venue_truncated(self):
        long_venue = "A" * 200
        candidates = normalize(make_raw(caption=f"at {long_venue}"))
        assert len(candidates["venue_name"]) == 110
        assert candidates["venue_name"] == long_venue[:110]


# ── Validator pipeline ──────────────────────────────────────────────────────


class TestValidatorPipeline:
    def test_instagram_snapshot_end_to_end(self):
        snapshot = make_snapshot(
            meta={"og:title": IG_OG_TITLE, "og:description": IG_OG_DESCRIPTION}
        )
        extractor = select_extractor(snapshot.url)
        record, reason = build_record(extractor.extract(snapshot))
        assert reason is None
        assert record.venue_name == "Casa Loma"
        assert record.category is EventCategory.MARKET_POPUP
        assert record.hashtags == ["birria", "longbeachfood", "tacos"]
        assert record.geo.source is GeoSource.CAPTION_TEXT
        assert record.provenance.platform == "instagram"
        assert len(record.provenance.content_hash) == 64

    def test_empty_page_rejected_with_reason(self):
        record, reason = build_record(make_raw())
        assert record is None
        assert "no human-readable text" in reason


# ── Offline Playwright integration (file:// fixtures, no network) ──────────


def offline_settings() -> IngestionSettings:
    return IngestionSettings(
        allow_file_urls=True,
        per_domain_delay_s=0,
        settle_timeout_ms=2_000,
    )


@pytest.mark.asyncio
async def test_fetch_jsonld_fixture_end_to_end():
    fixture_url = (FIXTURES / "jsonld_event.html").resolve().as_uri()
    async with SocialSessionManager(offline_settings()) as session:
        snapshot = await session.fetch(fixture_url)

    assert snapshot.status is FetchStatus.OK
    assert snapshot.meta.get("og:title") == "Live Jazz Night at The Harbor House"
    assert snapshot.jsonld, "JSON-LD block should have been parsed"
    assert any(c.role is TextRole.TITLE for c in snapshot.components)

    record, reason = build_record(GenericExtractor().extract(snapshot))
    assert reason is None
    assert record.venue_name == "The Harbor House"
    assert record.category is EventCategory.LIVE_MUSIC
    assert record.geo.lat == pytest.approx(33.7626)
    assert record.geo.source is GeoSource.PLATFORM_LOCATION_TAG
    assert "2026-06-19T20:00:00-07:00" in record.candidate_times


@pytest.mark.asyncio
async def test_fetch_instagram_fixture_through_dynamic_render():
    fixture_url = (FIXTURES / "instagram_post.html").resolve().as_uri()
    async with SocialSessionManager(offline_settings()) as session:
        snapshot = await session.fetch(fixture_url)

    assert snapshot.status is FetchStatus.OK
    assert "birria" in (snapshot.meta.get("og:description") or "")

    record, reason = build_record(InstagramExtractor().extract(snapshot))
    assert reason is None
    assert record.venue_name == "Casa Loma"
    assert record.provenance.platform == "instagram"


@pytest.mark.asyncio
async def test_non_http_url_rejected_without_navigation():
    settings = IngestionSettings(per_domain_delay_s=0)  # allow_file_urls stays False
    async with SocialSessionManager(settings) as session:
        snapshot = await session.fetch("file:///C:/anything.html")
    assert snapshot.status is FetchStatus.ERROR


# ════════════════════════════════════════════════════════════════════════════
# Phase 2 — LLM extraction layer
# ════════════════════════════════════════════════════════════════════════════


class FakeTransport:
    """Stand-in for the Ollama call. Yields canned responses; records each call.

    A response may be a str (returned) or an Exception (raised, simulating a
    connection/timeout error).
    """

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    def __call__(self, messages, format_schema):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("FakeTransport called more times than expected")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_extractor(*responses, **settings_overrides) -> LlmFieldExtractor:
    settings = IngestionSettings(**settings_overrides)
    return LlmFieldExtractor(settings, transport=FakeTransport(*responses))


# Payload the IG fixture would produce — used as the grounding text for cross-checks.
IG_PAYLOAD = {
    "platform": "instagram",
    "caption": IG_CAPTION,
    "hashtags": ["birria", "LongBeachFood", "tacos"],
}


def good_extraction_json(**overrides) -> str:
    data = {
        "venue_name": "Casa Loma",
        "core_theme": "Late-night birria tacos pop-up",
        "category": "market_popup",
        "raw_location_text": "4th St, Long Beach",
        "place_names": ["Long Beach"],
        "candidate_times": ["this Friday", "8pm"],
        "is_vague": False,
    }
    data.update(overrides)
    import json as _json

    return _json.dumps(data)


class TestExtractionSchema:
    def test_category_literal_matches_enum(self):
        # Drift guard: the LLM's allowed categories must equal EventCategory's values.
        assert set(CategoryLiteral) == {c.value for c in EventCategory}

    def test_format_schema_constrains_category_to_enum(self):
        schema = LlmExtraction.ollama_format_schema()
        assert schema["properties"]["category"] == {"enum": list(CategoryLiteral)}

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            LlmExtraction.model_validate({"category": "other", "surprise": 1})


class TestBuildLlmPayload:
    def test_strips_urls_and_caps_caption(self):
        long_caption = "Visit https://example.com/x now! " + ("y" * 2000)
        payload = build_llm_payload(make_raw(caption=long_caption))
        assert "http" not in payload["caption"]
        assert len(payload["caption"]) <= 1500

    def test_drops_description_equal_to_caption(self):
        payload = build_llm_payload(make_raw(caption="same text", description="same text"))
        assert "og_description" not in payload

    def test_caps_hashtags_and_includes_today(self):
        payload = build_llm_payload(
            make_raw(caption="x", hashtags=[f"t{i}" for i in range(40)])
        )
        assert len(payload["hashtags"]) == 15
        assert payload["today_date"] == NOW.date().isoformat()


class TestLlmExtractorLadder:
    def test_happy_path_returns_extraction(self):
        extractor = make_extractor(good_extraction_json())
        result = extractor.extract(IG_PAYLOAD)
        assert result.venue_name == "Casa Loma"
        assert result.category == "market_popup"
        assert len(extractor.transport.calls) == 1

    def test_repairs_fenced_json_without_extra_call(self):
        fenced = "```json\n" + good_extraction_json() + "\n```"
        extractor = make_extractor(fenced)
        result = extractor.extract(IG_PAYLOAD)
        assert result is not None and result.venue_name == "Casa Loma"
        assert len(extractor.transport.calls) == 1  # repaired locally, no re-prompt

    def test_corrective_reprompt_then_success(self):
        bad = '{"category": 12345}'  # category must be a string enum value
        extractor = make_extractor(bad, good_extraction_json())
        result = extractor.extract(IG_PAYLOAD)
        assert result is not None and result.venue_name == "Casa Loma"
        assert len(extractor.transport.calls) == 2  # one corrective retry happened

    def test_exhausted_retries_returns_none(self):
        extractor = make_extractor("not json at all", "still not json")
        assert extractor.extract(IG_PAYLOAD) is None
        assert len(extractor.transport.calls) == 2  # max_retries=1 → 2 attempts

    def test_transport_error_falls_back_immediately(self):
        extractor = make_extractor(httpx.ConnectError("ollama down"))
        assert extractor.extract(IG_PAYLOAD) is None
        assert len(extractor.transport.calls) == 1  # no retry on transport failure

    def test_hallucinated_fields_are_stripped(self):
        hallucinated = good_extraction_json(
            venue_name="Nonexistent Bistro",         # not in the payload text
            raw_location_text="Paris, France",        # not in the payload text
            place_names=["Long Beach", "Atlantis"],   # only Long Beach is grounded
        )
        result = make_extractor(hallucinated).extract(IG_PAYLOAD)
        assert result.venue_name is None
        assert result.raw_location_text is None
        assert result.place_names == ["Long Beach"]


class TestNormalizeWithLlm:
    def test_llm_overrides_heuristic_venue_and_category(self):
        # Heuristics alone would pick "market_popup" + "Casa Loma"; force a divergent
        # (grounded) LLM result and confirm it wins.
        raw = make_raw(caption=IG_CAPTION)
        ext = LlmExtraction(
            venue_name="Casa Loma",
            core_theme="birria night",
            category="food_drink",
            candidate_times=["this Friday"],
        )
        candidates = normalize(raw, llm_extraction=ext)
        assert candidates["category"] is EventCategory.FOOD_DRINK
        assert candidates["core_theme"] == "birria night"

    def test_invalid_llm_category_coerced_to_other(self):
        raw = make_raw(caption="something neutral")
        ext = LlmExtraction(category="not_a_real_category")
        assert normalize(raw, llm_extraction=ext)["category"] is EventCategory.OTHER

    def test_platform_location_outranks_llm(self):
        raw = make_raw(caption=IG_CAPTION, location_text="Long Beach, California")
        ext = LlmExtraction(raw_location_text="Somewhere Else", place_names=["Extra Place"])
        geo = normalize(raw, llm_extraction=ext)["geo"]
        assert geo.source is GeoSource.PLATFORM_LOCATION_TAG
        assert geo.raw_location_text == "Long Beach, California"
        assert "Extra Place" in geo.place_names  # still enriched

    def test_llm_supplies_location_when_heuristics_blank(self):
        raw = make_raw(caption="no obvious location here")  # heuristic geo = NONE
        ext = LlmExtraction(raw_location_text="Belmont Shore", place_names=["Belmont Shore"])
        geo = normalize(raw, llm_extraction=ext)["geo"]
        assert geo.source is GeoSource.CAPTION_TEXT
        assert geo.raw_location_text == "Belmont Shore"

    def test_structured_jsonld_venue_not_overridden_by_llm(self):
        # Regression: the model must not replace an authoritative schema.org Place
        # name with a fragment it pulled from the address line.
        raw = make_raw(platform="generic", venue_candidate="The Harbor House", caption="live jazz")
        ext = LlmExtraction(venue_name="Shoreline Dr", category="other")
        assert normalize(raw, llm_extraction=ext)["venue_name"] == "The Harbor House"

    def test_heuristic_category_kept_when_llm_unsure(self):
        # Regression: a clear keyword match must not regress to "other" just
        # because the small model hedged.
        raw = make_raw(caption="Friday jazz concert on the pier")  # heuristic → live_music
        ext = LlmExtraction(category="other")
        assert normalize(raw, llm_extraction=ext)["category"] is EventCategory.LIVE_MUSIC


class TestBuildRecordWithExtractor:
    def test_provenance_tagged_with_model_when_llm_used(self):
        snapshot = make_snapshot(
            meta={"og:title": IG_OG_TITLE, "og:description": IG_OG_DESCRIPTION}
        )
        raw = select_extractor(snapshot.url).extract(snapshot)
        extractor = make_extractor(good_extraction_json(), ollama_model="llama3.1:8b")
        record, reason = build_record(raw, extractor=extractor)
        assert reason is None
        assert record.provenance.extractor == "instagram/0.1+llm:llama3.1:8b"

    def test_provenance_tagged_heuristic_on_fallback(self):
        snapshot = make_snapshot(
            meta={"og:title": IG_OG_TITLE, "og:description": IG_OG_DESCRIPTION}
        )
        raw = select_extractor(snapshot.url).extract(snapshot)
        extractor = make_extractor(httpx.ConnectError("down"))  # forces fallback
        record, reason = build_record(raw, extractor=extractor)
        assert reason is None
        assert record.provenance.extractor == "instagram/0.1+heuristic"


# ── live test (skipped unless Ollama + the default model are available) ──────


def _ollama_ready() -> bool:
    from src.ingestion.cli import _check_ollama

    ok, _ = _check_ollama(IngestionSettings())
    return ok


@pytest.mark.skipif(not _ollama_ready(), reason="Ollama not running or model not pulled")
def test_live_extraction_against_real_model():
    extractor = LlmFieldExtractor(IngestionSettings())
    result = extractor.extract(IG_PAYLOAD)
    # Constrained decoding guarantees a valid schema; we don't assert exact values
    # (model wording varies) — only that it parsed and stayed grounded.
    assert result is not None
    assert result.category in CategoryLiteral
    if result.venue_name:
        assert "casa loma" in result.venue_name.lower()


# ════════════════════════════════════════════════════════════════════════════
# Phase 3 — geographic enrichment
# ════════════════════════════════════════════════════════════════════════════


class FakeGeocoder:
    """dict-backed stand-in for address_to_coords. Records every query."""

    def __init__(self, mapping: dict[str, tuple[float, float]]):
        self.mapping = {k.lower(): v for k, v in mapping.items()}
        self.calls: list[str] = []

    def __call__(self, query: str):
        self.calls.append(query)
        return self.mapping.get(query.strip().lower(), (None, None))


def make_enricher(mapping: dict, **settings_overrides) -> GeoEnricher:
    settings = IngestionSettings(**settings_overrides)
    return GeoEnricher(settings, geocoder=FakeGeocoder(mapping))


def geo(**overrides) -> GeoContext:
    return GeoContext(**overrides)


class TestGeoEnricher:
    def test_explicit_coords_skip_geocoder(self):
        enr = make_enricher({})
        g = geo(lat=33.77, lng=-118.19, source=GeoSource.PLATFORM_LOCATION_TAG, confidence=0.9)
        out = enr._resolve(g, venue_name="The Harbor House")
        assert out.resolution is GeoResolution.EXPLICIT
        assert out.confidence == 0.9
        assert enr._geocoder.calls == []  # never called Nominatim

    def test_explicit_confidence_floored_to_0_9(self):
        enr = make_enricher({})
        g = geo(lat=1.0, lng=2.0, confidence=0.5)
        assert enr._resolve(g, venue_name="x").confidence == 0.9

    def test_specific_clue_geocoded(self):
        enr = make_enricher({"4th St, Long Beach": (33.77, -118.19)})
        g = geo(
            raw_location_text="4th St, Long Beach",
            place_names=["4th St", "Long Beach"],
            source=GeoSource.CAPTION_TEXT,
            confidence=0.6,
        )
        out = enr._resolve(g, venue_name="Casa Loma")
        assert out.resolution is GeoResolution.GEOCODED
        assert out.confidence == 0.75
        assert (out.lat, out.lng) == (33.77, -118.19)
        assert enr._geocoder.calls == ["4th St, Long Beach"]  # first specific tier hit

    def test_venue_miss_falls_back_to_city(self):
        enr = make_enricher({"Long Beach": (33.76, -118.19)})
        g = geo(place_names=["Nonexistent Venue", "Long Beach"], source=GeoSource.CAPTION_TEXT, confidence=0.5)
        out = enr._resolve(g, venue_name="Casa Loma")
        assert out.resolution is GeoResolution.GEOCODED_BROAD
        assert out.confidence == 0.45
        assert (out.lat, out.lng) == (33.76, -118.19)
        assert "Long Beach" in enr._geocoder.calls  # broad tier was tried after specifics

    def test_total_miss_unresolved_and_decayed(self):
        enr = make_enricher({})  # nothing resolves
        g = geo(place_names=["Nowhere"], source=GeoSource.CAPTION_TEXT, confidence=0.6)
        out = enr._resolve(g, venue_name="Ghost Kitchen")
        assert out.resolution is GeoResolution.UNRESOLVED
        assert out.lat is None and out.lng is None
        assert out.confidence == 0.3  # 0.6 * 0.5

    def test_nothing_to_geocode_leaves_confidence(self):
        enr = make_enricher({})
        g = geo(source=GeoSource.HASHTAG, confidence=0.2)  # no place_names, no raw_location_text
        out = enr._resolve(g, venue_name="Whatever")
        assert out.resolution is GeoResolution.UNRESOLVED
        assert out.confidence == 0.2  # unchanged
        assert enr._geocoder.calls == []

    def test_query_cap_limits_specific_calls(self):
        enr = make_enricher({}, max_geocode_queries=1)
        g = geo(
            raw_location_text="Specific Address",
            place_names=["Neighborhood", "Long Beach"],
            confidence=0.6,
        )
        enr._resolve(g, venue_name="Some Venue")
        # 1 specific tier (capped) + the broad city fallback = 2 total
        assert len(enr._geocoder.calls) == 2

    def test_cache_dedupes_repeated_city(self):
        enr = make_enricher({"Long Beach": (33.76, -118.19)})
        g = geo(place_names=["Long Beach"], confidence=0.5)
        enr._resolve(g, venue_name="Long Beach")  # venue==city → only broad "Long Beach"
        enr._resolve(g, venue_name="Long Beach")  # second time should hit the cache
        assert enr._geocoder.calls == ["Long Beach"]  # fetched once

    def test_enrich_returns_updated_record(self):
        enr = make_enricher({"Belmont Shore, Long Beach": (33.76, -118.13)})
        record = EventInspiration(
            **make_record_kwargs(
                geo=geo(
                    raw_location_text="Belmont Shore, Long Beach",
                    place_names=["Belmont Shore", "Long Beach"],
                    source=GeoSource.CAPTION_TEXT,
                    confidence=0.6,
                )
            )
        )
        out = enr.enrich(record)
        assert isinstance(out, EventInspiration)
        assert out.geo.resolution is GeoResolution.GEOCODED
        assert (out.geo.lat, out.geo.lng) == (33.76, -118.13)
        assert record.geo.lat is None  # original record untouched (model_copy)


# ── live geocode test (skipped unless real Nominatim is reachable) ───────────


def _nominatim_ready() -> bool:
    try:
        from src.services.location_service import address_to_coords

        lat, _ = address_to_coords("Long Beach, California")
        return lat is not None
    except Exception:
        return False


@pytest.mark.skipif(not _nominatim_ready(), reason="Nominatim not reachable (e.g. network TLS interception)")
def test_live_geocode_against_real_nominatim():
    enr = GeoEnricher(IngestionSettings())
    g = GeoContext(
        raw_location_text="Long Beach, California",
        place_names=["Long Beach", "California"],
        source=GeoSource.CAPTION_TEXT,
        confidence=0.6,
    )
    out = enr._resolve(g, venue_name="")
    assert out.resolution in (GeoResolution.GEOCODED, GeoResolution.GEOCODED_BROAD)
    assert out.lat is not None and 33.0 < out.lat < 34.5  # Long Beach is ~33.77 N


# ════════════════════════════════════════════════════════════════════════════
# Phase 4 — temporal parsing (fully offline; dateparser does no network I/O)
# ════════════════════════════════════════════════════════════════════════════

LA = ZoneInfo("America/Los_Angeles")
# Wed Jun 10 2026 — "this Friday" resolves to Fri Jun 12.
FETCHED = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
# Fixed execution reference so future/expired classification is deterministic.
NOW_REF = datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)


def timed_record(candidate_times: list[str], fetched_at: datetime = FETCHED) -> EventInspiration:
    return EventInspiration(
        **make_record_kwargs(
            candidate_times=candidate_times,
            provenance=make_provenance(fetched_at=fetched_at),
        )
    )


def make_resolver(now: datetime = NOW_REF, **settings_overrides) -> TemporalResolver:
    return TemporalResolver(IngestionSettings(**settings_overrides), now=now)


def _local(dt: datetime):
    return dt.astimezone(LA)


class TestParseDatetime:
    def test_returns_utc_datetime_and_period(self):
        dt, period = TimeParser().parse_datetime("8pm", relative_base=FETCHED.replace(tzinfo=None))
        assert dt is not None and dt.tzinfo is not None
        assert period == "time"

    def test_unparseable_returns_none(self):
        assert TimeParser().parse_datetime("not a time at all zzz") == (None, None)

    def test_extract_time_unchanged(self):
        # The original display-string API must keep working for coordinator.py.
        out = TimeParser().extract_time("this Friday at 8pm")
        assert isinstance(out, str) and out != ""


class TestTemporalResolver:
    def test_combines_day_and_time_fragments(self):
        rec, reason = make_resolver().resolve(timed_record(["this Friday", "8pm"]))
        assert reason is None
        sched = rec.schedule
        assert sched.status is ScheduleStatus.SCHEDULED
        assert sched.time_known is True
        local = _local(sched.start_utc)
        assert local.weekday() == 4 and local.hour == 20  # Friday 8pm local
        assert sched.end_utc == sched.start_utc + timedelta(hours=2)
        assert "+" in sched.parsed_from  # combined two fragments

    def test_full_iso_used_directly(self):
        rec, reason = make_resolver().resolve(timed_record(["2026-06-19T20:00:00-07:00"]))
        assert reason is None
        # -07:00 → UTC is +7h → 2026-06-20T03:00:00Z, used verbatim.
        assert rec.schedule.start_utc == datetime(2026, 6, 20, 3, 0, tzinfo=timezone.utc)
        assert rec.schedule.time_known is True

    def test_date_only_defaults_hour_and_flags_unknown_time(self):
        rec, reason = make_resolver().resolve(timed_record(["June 5"]))
        assert reason is None
        assert rec.schedule.status is ScheduleStatus.SCHEDULED
        assert rec.schedule.time_known is False
        assert _local(rec.schedule.start_utc).hour == 18  # default 6pm local

    def test_expired_event_rejected(self):
        rec, reason = make_resolver().resolve(timed_record(["2020-01-01T10:00:00-08:00"]))
        assert reason is not None and reason.startswith("event already passed")
        assert rec.schedule is None  # original record returned unchanged, not written

    def test_expired_kept_when_rejection_disabled(self):
        rec, reason = make_resolver(reject_expired=False).resolve(
            timed_record(["2020-01-01T10:00:00-08:00"])
        )
        assert reason is None
        assert rec.schedule.status is ScheduleStatus.SCHEDULED  # past, but not rejected

    def test_no_parseable_time_is_unscheduled(self):
        rec, reason = make_resolver().resolve(timed_record([]))
        assert reason is None
        assert rec.schedule.status is ScheduleStatus.UNSCHEDULED
        assert rec.schedule.start_utc is None

    def test_relative_base_uses_fetched_at(self):
        # Same expression, two capture dates → two different resolved days.
        early = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
        late = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
        resolver = make_resolver(now=datetime(2026, 6, 1, tzinfo=timezone.utc))
        r1, _ = resolver.resolve(timed_record(["tomorrow"], fetched_at=early))
        r2, _ = resolver.resolve(timed_record(["tomorrow"], fetched_at=late))
        assert _local(r1.schedule.start_utc).date() != _local(r2.schedule.start_utc).date()
        assert _local(r1.schedule.start_utc).day == 11  # day after Jun 10
        assert _local(r2.schedule.start_utc).day == 21  # day after Jun 20

    def test_provenance_tagged_after_resolution(self):
        rec, _ = make_resolver().resolve(timed_record(["this Friday", "8pm"]))
        assert rec.provenance.temporal_parser is not None
        assert rec.provenance.temporal_parser.startswith("timeparser")


# ════════════════════════════════════════════════════════════════════════════
# Phase 5 — ChromaDB vector sink
# ════════════════════════════════════════════════════════════════════════════

import hashlib as _hashlib

from src.ingestion.schemas.results import IngestionResult


def fake_embedder(texts: list[str]) -> list[list[float]]:
    """Deterministic 8-dim vectors from a hash — no Ollama, no network."""
    out = []
    for t in texts:
        h = _hashlib.sha256(t.encode()).digest()
        out.append([b / 255.0 for b in h[:8]])
    return out


def make_chroma_sink(tmp_path, collection="event_inspirations"):
    from src.ingestion.sinks.chroma_sink import ChromaSink

    settings = IngestionSettings(chroma_path=str(tmp_path), chroma_collection=collection)
    return ChromaSink(settings, embedder=fake_embedder)


def chroma_record(content_text="birria pop-up", category=EventCategory.MARKET_POPUP, **geo_over):
    geo = GeoContext(
        raw_location_text="4th St, Long Beach",
        place_names=["4th St", "Long Beach"],
        source=GeoSource.CAPTION_TEXT,
        confidence=0.6,
        **geo_over,
    )
    return EventInspiration(
        **make_record_kwargs(
            category=category,
            geo=geo,
            hashtags=["birria", "tacos"],
            provenance=make_provenance(content_hash=content_hash(content_text)),
        )
    )


class TestChromaSink:
    def test_add_and_get_roundtrip(self, tmp_path):
        sink = make_chroma_sink(tmp_path)
        rec = chroma_record()
        rid = sink.add(rec)
        assert sink.count() == 1
        assert rid == rec.provenance.content_hash
        row = sink.get(rid)
        assert row["metadata"]["venue_name"] == rec.venue_name
        assert row["metadata"]["category"] == "market_popup"
        assert row["metadata"]["tags"] == "birria,tacos"

    def test_dedup_by_content_hash(self, tmp_path):
        sink = make_chroma_sink(tmp_path)
        sink.add(chroma_record(content_text="same post"))
        sink.add(chroma_record(content_text="same post"))  # identical hash → upsert
        assert sink.count() == 1
        sink.add(chroma_record(content_text="different post"))
        assert sink.count() == 2

    def test_metadata_is_chroma_safe(self, tmp_path):
        sink = make_chroma_sink(tmp_path)
        rec = chroma_record()
        meta = sink.get(sink.add(rec))["metadata"]
        for key, value in meta.items():
            assert isinstance(value, (str, int, float, bool)), f"{key}={value!r} not Chroma-safe"
        assert "tags" in meta and isinstance(meta["tags"], str)  # list joined to string

    def test_scheduled_record_has_epoch(self, tmp_path):
        sink = make_chroma_sink(tmp_path)
        rec, _ = make_resolver().resolve(timed_record(["2026-06-19T20:00:00-07:00"]))
        meta = sink.get(sink.add(rec))["metadata"]
        assert meta["schedule_status"] == "scheduled"
        assert isinstance(meta["start_epoch"], int)

    def test_query_returns_seeded_record_with_filter(self, tmp_path):
        sink = make_chroma_sink(tmp_path)
        sink.add(chroma_record(content_text="a", category=EventCategory.MARKET_POPUP))
        sink.add(chroma_record(content_text="b", category=EventCategory.LIVE_MUSIC))
        hits = sink.query("birria tacos", k=5, where={"category": "market_popup"})
        assert len(hits) == 1
        assert hits[0]["metadata"]["category"] == "market_popup"

    def test_write_skips_none_record(self, tmp_path):
        sink = make_chroma_sink(tmp_path)
        sink.write(IngestionResult(url="x", fetch_status=FetchStatus.LOGIN_WALL))
        assert sink.count() == 0

    def test_write_persists_record(self, tmp_path):
        sink = make_chroma_sink(tmp_path)
        sink.write(IngestionResult(url="x", fetch_status=FetchStatus.OK, record=chroma_record()))
        assert sink.count() == 1


# ── live embedder test (skipped unless Ollama embeddings are reachable) ──────


def _ollama_embeddings_ready() -> bool:
    try:
        import httpx as _httpx

        settings = IngestionSettings()
        r = _httpx.post(
            f"{settings.ollama_url}/api/embeddings",
            json={"model": settings.embed_model, "prompt": "ping"},
            timeout=10.0,
        )
        return r.status_code == 200 and "embedding" in r.json()
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_embeddings_ready(), reason="Ollama embeddings not reachable")
def test_live_chroma_roundtrip_with_real_embeddings(tmp_path):
    from src.ingestion.sinks.chroma_sink import ChromaSink

    settings = IngestionSettings(chroma_path=str(tmp_path), chroma_collection="live_test")
    sink = ChromaSink(settings)  # real OllamaEmbedder
    sink.add(chroma_record(content_text="late night birria tacos pop-up"))
    hits = sink.query("tacos", k=1)
    assert len(hits) == 1 and sink.count() == 1


# ════════════════════════════════════════════════════════════════════════════
# Phase 6 — Discord retrieval, ranking & formatting
# ════════════════════════════════════════════════════════════════════════════

from src.ingestion.serving.discord_format import format_recommendations
from src.ingestion.serving.recommender import (
    Recommendation,
    RecommendationService,
    extract_query,
    looks_like_request,
)


class StubSink:
    """Returns predetermined hits with controlled distances — lets us test the
    ranking/spam logic deterministically (fake embeddings give arbitrary distances)."""

    def __init__(self, hits: list[dict]):
        self.hits = hits
        self.calls: list[tuple] = []

    def query(self, text, k=5, where=None):
        self.calls.append((text, where))
        return list(self.hits)


def hit(content_hash="h1", venue="Casa Loma", category="market_popup", distance=0.2,
        start_epoch=None):
    meta = {
        "content_hash": content_hash, "venue_name": venue, "category": category,
        "core_theme": "late-night birria pop-up", "source_url": "https://example.com/p/1",
    }
    if start_epoch is not None:
        meta["start_epoch"] = start_epoch
        meta["end_epoch"] = start_epoch + 7200
        meta["schedule_status"] = "scheduled"
    return {"document": "doc", "metadata": meta, "distance": distance}


def rec_service(hits, **overrides):
    settings = IngestionSettings(**overrides)
    return RecommendationService(StubSink(hits), settings)


class TestQueryExtraction:
    def test_strips_mention_command_and_stopwords(self):
        q = extract_query("<@123> /events where should we eat tacos tonight")
        assert "tacos" in q and "where" not in q and "<@123>" not in q and "/events" not in q

    def test_keeps_hashtags(self):
        assert "#birria" in extract_query("anything #birria")

    def test_intent_gate(self):
        assert looks_like_request("where can we eat tonight")
        assert not looks_like_request("good morning everyone")


class TestRecommendationService:
    def test_relevant_query_returns_recommendation(self):
        svc = rec_service([hit(distance=0.2)])
        result = svc.recommend("c1", "birria tacos", mode="command", now=1000.0)
        assert not result.suppressed
        assert result.recommendations[0].venue_name == "Casa Loma"

    def test_distance_over_threshold_suppressed(self):
        svc = rec_service([hit(distance=1.5)], rec_max_distance=0.9)
        result = svc.recommend("c1", "birria tacos", mode="command", now=1000.0)
        assert result.suppressed and result.reason == "no relevant match"

    def test_auto_intent_gate(self):
        svc = rec_service([hit()])
        assert svc.recommend("c1", "good morning", mode="auto", now=1000.0).reason == "no request intent"
        # explicit command bypasses the intent gate
        assert not svc.recommend("c1", "good morning", mode="command", now=1000.0).suppressed

    def test_auto_cooldown(self):
        svc = rec_service([hit()], rec_cooldown_s=100, rec_dedup_window_s=0)
        assert not svc.recommend("c1", "eat tacos", mode="auto", now=1000.0).suppressed
        assert svc.recommend("c1", "eat tacos", mode="auto", now=1050.0).reason == "cooldown"
        assert not svc.recommend("c1", "eat tacos", mode="auto", now=1200.0).suppressed  # past cooldown

    def test_dedup_within_window(self):
        svc = rec_service([hit(content_hash="dup")], rec_dedup_window_s=10_000)
        assert not svc.recommend("c1", "tacos", mode="command", now=1000.0).suppressed
        # same event again in the same channel → filtered out
        assert svc.recommend("c1", "tacos", mode="command", now=1100.0).reason == "no relevant match"

    def test_max_results_cap(self):
        hits = [hit(content_hash=f"h{i}", distance=0.1) for i in range(10)]
        svc = rec_service(hits, rec_max_results=3)
        result = svc.recommend("c1", "tacos", mode="command", now=1000.0)
        assert len(result.recommendations) == 3

    def test_empty_query_suppressed(self):
        svc = rec_service([hit()])
        assert svc.recommend("c1", "the a to of", mode="command", now=1000.0).reason == "empty query"


class TestDiscordFormat:
    def test_scheduled_recommendation_markdown(self):
        rec = Recommendation(
            content_hash="h1", venue_name="Casa Loma", category="market_popup",
            core_theme="late-night birria pop-up", source_url="https://example.com/p/1",
            distance=0.2, start_epoch=1781000000, end_epoch=1781007200,
        )
        md = format_recommendations([rec])
        assert "**🛍️ Casa Loma**" in md
        assert "market popup" in md
        assert "<t:1781000000:F>" in md
        assert "<https://example.com/p/1>" in md

    def test_unscheduled_shows_tbd(self):
        rec = Recommendation("h1", "X", "other", "theme", "https://e.com", 0.3)
        assert "time TBD" in format_recommendations([rec])

    def test_empty_is_friendly(self):
        assert "couldn't find" in format_recommendations([]).lower()


def test_recommend_endpoint_via_testclient(monkeypatch):
    from fastapi.testclient import TestClient
    import src.ingestion.serving.app as serving_app

    monkeypatch.setattr(
        serving_app, "_service", rec_service([hit(start_epoch=1781000000)]), raising=False
    )
    client = TestClient(serving_app.app)

    assert client.get("/health").json()["status"] == "ok"
    resp = client.post("/recommend", json={"channel_id": "c1", "message": "birria tacos", "mode": "command"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["suppressed"] is False
    assert body["recommendations"][0]["venue_name"] == "Casa Loma"
    assert "Casa Loma" in body["markdown"]


# ════════════════════════════════════════════════════════════════════════════
# Phase 7 — orchestration & diagnostic reporting
# ════════════════════════════════════════════════════════════════════════════


def _run_report(*results) -> RunReport:
    return RunReport(
        results=list(results),
        started_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        duration_s=1.5,
        out_path=Path("data/inspirations.jsonl"),
    )


class TestRunReport:
    def test_buckets_results_correctly(self):
        report = _run_report(
            IngestionResult(url="u1", fetch_status=FetchStatus.OK, record=chroma_record()),
            IngestionResult(url="u2", fetch_status=FetchStatus.OK, rejection_reason="event already passed: 2020-01-01"),
            IngestionResult(url="u3", fetch_status=FetchStatus.LOGIN_WALL),
            IngestionResult(url="u4", fetch_status=FetchStatus.TIMEOUT),
        )
        assert len(report.validated) == 1
        assert len(report.rejected) == 1
        assert len(report.connectivity_failures) == 2

    def test_render_has_diagnostic_sections(self):
        report = _run_report(
            IngestionResult(url="u1", fetch_status=FetchStatus.OK, record=chroma_record()),
            IngestionResult(url="u2", fetch_status=FetchStatus.OK, rejection_reason="event already passed: 2020-01-01"),
            IngestionResult(url="u3", fetch_status=FetchStatus.LOGIN_WALL),
            IngestionResult(url="u4", fetch_status=FetchStatus.TIMEOUT),
        )
        text = report.render()
        assert "INGESTION RUN REPORT" in text
        assert "Validation rejections:" in text and "event already passed" in text
        assert "Connectivity / access issues:" in text
        assert "login_wall" in text and "timeout" in text
        assert "Records written:" in text  # geo/schedule breakdown for the validated record

    def test_result_line_variants(self):
        ok = IngestionResult(url="u1", fetch_status=FetchStatus.OK, record=chroma_record())
        assert result_line(ok).startswith("[ok]")
        rej = IngestionResult(url="u2", fetch_status=FetchStatus.OK, rejection_reason="bad")
        assert result_line(rej).startswith("[reject]")
        wall = IngestionResult(url="u3", fetch_status=FetchStatus.LOGIN_WALL)
        assert result_line(wall).startswith("[login_wall]")


@pytest.mark.asyncio
async def test_pipeline_runs_both_fixtures_end_to_end():
    settings = IngestionSettings(
        allow_file_urls=True, per_domain_delay_s=0, settle_timeout_ms=2000
    )
    pipeline = IngestionPipeline(
        settings, temporal_resolver=TemporalResolver(settings)  # no LLM / geo / sinks
    )
    urls = [
        (FIXTURES / "instagram_post.html").resolve().as_uri(),
        (FIXTURES / "jsonld_event.html").resolve().as_uri(),
    ]
    report = await pipeline.run(urls)
    assert len(report.results) == 2
    assert len(report.validated) == 2
    assert len(report.connectivity_failures) == 0
    # both fixtures carry parseable times → scheduled
    assert all(r.record.schedule.status is ScheduleStatus.SCHEDULED for r in report.validated)
    assert "INGESTION RUN REPORT" in report.render()
