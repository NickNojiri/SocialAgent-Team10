"""Offline tests for location awareness: near-me ranking + midpoint mode.

No network: the geocoder is injected as a dict lookup, mirroring how
TestGeoEnricher fakes Nominatim.
"""

import pytest

from src.ingestion.config import IngestionSettings
from src.ingestion.serving.discord_format import format_recommendations
from src.ingestion.serving.recommender import (
    RecommendationService,
    _haversine_km,
    extract_origin_text,
    user_locations,
)

# Real-ish anchors (Long Beach / Anaheim / Santa Monica)
LB = (33.77, -118.19)
ANAHEIM = (33.83, -117.91)
SANTA_MONICA = (34.02, -118.49)

GEO = {
    "long beach": LB,
    "downtown lb": LB,
    "anaheim": ANAHEIM,
    "santa monica": SANTA_MONICA,
}


def fake_geocode(place: str):
    return GEO.get(place.lower().strip(), (None, None))


class _FakeCollection:
    def __init__(self, results):
        self._results = results

    def get(self, include=None):
        return {
            "ids": [r["metadata"]["content_hash"] for r in self._results],
            "metadatas": [r["metadata"] for r in self._results],
        }


class MappedSink:
    """Returns canned hits: one spot per anchor + one with no coordinates."""

    def __init__(self):
        self._results = [
            {"metadata": {"content_hash": "sm", "venue_name": "SM Pier Tacos",
                          "lat": SANTA_MONICA[0], "lng": SANTA_MONICA[1]}, "distance": 0.1},
            {"metadata": {"content_hash": "lb", "venue_name": "LB Birria",
                          "lat": LB[0], "lng": LB[1]}, "distance": 0.2},
            {"metadata": {"content_hash": "ana", "venue_name": "Anaheim Mazesoba",
                          "lat": ANAHEIM[0], "lng": ANAHEIM[1], "attended": True}, "distance": 0.3},
            {"metadata": {"content_hash": "noc", "venue_name": "Mystery Spot"}, "distance": 0.25},
        ]
        self.collection = _FakeCollection(self._results)

    def query(self, text, k, where=None):
        return self._results


def make_service():
    settings = IngestionSettings(rec_max_distance=0.9, rec_max_results=4)
    return RecommendationService(MappedSink(), settings, geocode_fn=fake_geocode)


class TestOriginExtraction:
    def test_im_at_variants(self):
        assert extract_origin_text("im at long beach tonight") == "long beach"
        assert extract_origin_text("I'm in Downtown LB rn") == "Downtown LB"
        assert extract_origin_text("we're near Anaheim, anything good?") == "Anaheim"
        assert extract_origin_text("tacos around santa monica?") == "santa monica"

    def test_no_place_is_none(self):
        assert extract_origin_text("who wants tacos") is None
        assert extract_origin_text("") is None

    def test_user_locations_from_transcript(self):
        transcript = (
            "nick: im at long beach\n"
            "sam: down!! i'm in anaheim tho\n"
            "jo: I want tacos\n"
        )
        # "tho" is trailing noise and gets stripped from sam's place
        assert user_locations(transcript) == {"nick": "long beach", "sam": "anaheim"}


class TestHaversine:
    def test_lb_to_anaheim_is_plausible(self):
        d = _haversine_km(*LB, *ANAHEIM)
        assert 20 < d < 40          # ~27 km as the crow flies

    def test_zero_distance(self):
        assert _haversine_km(*LB, *LB) == 0


class TestNearMeRanking:
    def test_near_phrase_ranks_by_distance(self):
        service = make_service()
        result = service.recommend("ch1", "tacos near long beach", mode="command")
        names = [r.venue_name for r in result.recommendations]
        # LB first, Anaheim next, Santa Monica after; the no-coords spot sinks last
        assert names[0] == "LB Birria"
        assert names[1] == "Anaheim Mazesoba"
        assert names[-1] == "Mystery Spot"
        assert result.recommendations[0].distance_km == 0
        assert result.recommendations[1].distance_km > 20

    def test_no_location_keeps_relevance_order(self):
        service = make_service()
        result = service.recommend("ch1", "tacos", mode="command")
        assert [r.venue_name for r in result.recommendations][0] == "SM Pier Tacos"
        assert all(r.distance_km is None for r in result.recommendations)

    def test_unknown_place_degrades_gracefully(self):
        service = make_service()
        result = service.recommend("ch1", "tacos near narnia", mode="command")
        assert not result.suppressed
        assert all(r.distance_km is None for r in result.recommendations)

    def test_distance_appears_in_markdown(self):
        service = make_service()
        result = service.recommend("ch1", "tacos near long beach", mode="command")
        assert "km away" in format_recommendations(result.recommendations)


class TestMidpointPlan:
    def _plan(self, transcript, monkeypatch):
        import src.ingestion.serving.recommender as rec_mod

        # keep /plan offline: no Ollama request synthesis
        monkeypatch.setattr(rec_mod, "synthesize_request", lambda t, s: {"vibe": "tacos"})
        return make_service().plan("ch1", transcript)

    def test_two_users_meet_in_the_middle(self, monkeypatch):
        plan = self._plan("nick: im at long beach\nsam: i'm in anaheim\njo: tacos!!", monkeypatch)
        assert "nick (long beach)" in plan.request["midpoint_of"]
        assert "sam (anaheim)" in plan.request["midpoint_of"]
        names = [r.venue_name for r in plan.recommendations]
        # Midpoint of LB+Anaheim: both are near-equidistant; Santa Monica is far
        assert names.index("SM Pier Tacos") > max(
            names.index("LB Birria"), names.index("Anaheim Mazesoba")
        )
        assert all(
            r.distance_km is not None for r in plan.recommendations if r.venue_name != "Mystery Spot"
        )

    def test_one_user_pulls_toward_them(self, monkeypatch):
        plan = self._plan("sam: i'm in anaheim, tacos?", monkeypatch)
        assert plan.request["near"] == "anaheim"
        assert [r.venue_name for r in plan.recommendations][0] == "Anaheim Mazesoba"

    def test_no_locations_no_annotation(self, monkeypatch):
        plan = self._plan("jo: tacos??\nnick: yes", monkeypatch)
        assert "midpoint_of" not in plan.request
        assert "near" not in plan.request


class TestNamePinning:
    def _plan(self, transcript, monkeypatch):
        import src.ingestion.serving.recommender as rec_mod

        monkeypatch.setattr(rec_mod, "synthesize_request", lambda t, s: {"vibe": "tacos"})
        return make_service().plan("ch1", transcript)

    def test_mentioned_venue_is_pinned_first(self, monkeypatch):
        plan = self._plan("nick: LB Birria was so nice last time\njo: down, tacos", monkeypatch)
        first = plan.recommendations[0]
        assert first.venue_name == "LB Birria"
        assert first.pinned is True
        # no duplicate of the pinned spot later in the list
        assert [r.venue_name for r in plan.recommendations].count("LB Birria") == 1

    def test_attended_spots_outrank_untried_ones(self, monkeypatch):
        plan = self._plan("jo: tacos tonight??", monkeypatch)
        names = [r.venue_name for r in plan.recommendations]
        # Anaheim Mazesoba is attended=True → leads the unpinned picks
        assert names[0] == "Anaheim Mazesoba"
        assert plan.recommendations[0].attended is True

    def test_no_mention_no_pins(self, monkeypatch):
        plan = self._plan("jo: tacos tonight??", monkeypatch)
        assert all(not r.pinned for r in plan.recommendations)
