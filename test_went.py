"""Offline tests for the went-there loop — the 100 Nights Out counter.

lock → followup due (once) → two confirmations → attended night counted.
Runs against a real ChromaSink on tmp_path with a fake embedder; the admin
endpoints are exercised through TestClient with a pre-seeded per-guild sink.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
from src.ingestion.config import IngestionSettings
from src.ingestion.schemas.inspiration import (
    EventCategory, EventInspiration, GeoContext, SourceProvenance, content_hash,
)
from src.ingestion.sinks.chroma_sink import ChromaSink

GUILD = "test-went-guild"
NOW = 1_800_000_000   # fixed "now" for deterministic due-ness


def fake_embedder(texts):
    return [[float(len(t) % 11) for _ in range(8)] for t in texts]


def seed_record(venue="Casa Loma"):
    return EventInspiration(
        venue_name=venue,
        core_theme="late-night birria tacos",
        category=EventCategory.FOOD_DRINK,
        geo=GeoContext(),
        provenance=SourceProvenance(
            source_url="https://www.instagram.com/p/X/",
            platform="instagram",
            fetched_at=datetime.now(timezone.utc),
            content_hash=content_hash(venue),
            extractor="instagram/0.1",
        ),
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    sink = ChromaSink(
        IngestionSettings(chroma_path=str(tmp_path)), embedder=fake_embedder
    )
    monkeypatch.setitem(admin._sinks, GUILD, sink)
    test_client = TestClient(admin.app)
    event_id = sink.add(seed_record())
    return test_client, event_id


def _lock(client, event_id, end_epoch):
    resp = client.post(
        f"/api/events/{event_id}/lock",
        json={"guild_id": GUILD, "channel_id": "123", "end_epoch": end_epoch},
    )
    assert resp.status_code == 200


class TestWentLoop:
    def test_followup_due_only_after_the_night_and_only_once(self, client):
        tc, eid = client
        _lock(tc, eid, end_epoch=NOW)

        # Same evening → not due yet (8h grace)
        due = tc.get("/api/followups", params={"guild_id": GUILD, "now": NOW + 3600}).json()["due"]
        assert due == []

        # Next morning → due exactly once, with the channel to ask in
        morning = NOW + 10 * 3600
        due = tc.get("/api/followups", params={"guild_id": GUILD, "now": morning}).json()["due"]
        assert len(due) == 1
        assert due[0]["id"] == eid
        assert due[0]["venue"] == "Casa Loma"
        assert due[0]["channel_id"] == "123"

        # Never asked twice
        again = tc.get("/api/followups", params={"guild_id": GUILD, "now": morning + 60}).json()["due"]
        assert again == []

    def test_two_confirmations_make_an_official_night(self, client):
        tc, eid = client
        _lock(tc, eid, end_epoch=NOW)

        first = tc.post(
            f"/api/events/{eid}/went", params={"guild_id": GUILD},
            json={"user_id": "1", "user_name": "nick"},
        ).json()
        assert first["confirmations"] == 1
        assert first["attended"] is False
        assert first["nights"] == 0

        # Same user again → still one confirmation (no self-confirming a night)
        dup = tc.post(
            f"/api/events/{eid}/went", params={"guild_id": GUILD},
            json={"user_id": "1", "user_name": "nick"},
        ).json()
        assert dup["confirmations"] == 1

        second = tc.post(
            f"/api/events/{eid}/went", params={"guild_id": GUILD},
            json={"user_id": "2", "user_name": "sam"},
        ).json()
        assert second["confirmations"] == 2
        assert second["attended"] is True
        assert second["nights"] == 1
        assert second["venue"] == "Casa Loma"

        assert tc.get("/api/nights", params={"guild_id": GUILD}).json() == {"nights": 1}

    def test_didnt_happen_never_counts(self, client):
        tc, eid = client
        _lock(tc, eid, end_epoch=NOW)
        resp = tc.post(
            f"/api/events/{eid}/went", params={"guild_id": GUILD},
            json={"user_id": "1", "happened": False},
        ).json()
        assert resp["attended"] is False
        assert tc.get("/api/nights", params={"guild_id": GUILD}).json()["nights"] == 0

    def test_unlocked_events_are_never_followed_up(self, client):
        tc, _eid = client   # seeded but never locked
        due = tc.get("/api/followups", params={"guild_id": GUILD, "now": NOW * 2}).json()["due"]
        assert due == []
