import pytest
from fastapi.testclient import TestClient
from src.ingestion.serving.app import app
import src.ingestion.serving.app as serving_app
from src.ingestion.config import IngestionSettings
from src.ingestion.serving.recommender import RecommendationService

class StubChromaSink:
    def __init__(self):
        self.invocations = []
        self._results = []

    def set_results(self, results):
        self._results = results

    def query(self, text: str, k: int, where: dict = None):
        self.invocations.append((text, k, where))
        return self._results

def test_recommend_endpoint_manual_intent(monkeypatch):
    sink = StubChromaSink()
    sink.set_results([
        {
            "metadata": {"content_hash": "a1", "venue_name": "Pub", "category": "nightlife"},
            "distance": 0.1
        }
    ])
    
    settings = IngestionSettings(rec_cooldown_s=10, rec_max_distance=0.5, rec_max_results=3)
    service = RecommendationService(sink, settings)
    monkeypatch.setattr(serving_app, "_service", service)
    
    client = TestClient(app)
    
    # 1. Manual mode (command) bypasses intent gate
    resp = client.post("/recommend", json={"channel_id": "ch1", "message": "hello", "mode": "command"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["suppressed"] is False
    assert len(data["recommendations"]) == 1

def test_recommend_endpoint_auto_intent_gate(monkeypatch):
    sink = StubChromaSink()
    settings = IngestionSettings(rec_cooldown_s=10, rec_max_distance=0.5, rec_max_results=3)
    service = RecommendationService(sink, settings)
    monkeypatch.setattr(serving_app, "_service", service)
    
    client = TestClient(app)
    
    # Auto mode with non-trigger word
    resp = client.post("/recommend", json={"channel_id": "ch1", "message": "hello there", "mode": "auto"})
    data = resp.json()
    assert data["suppressed"] is True
    assert data["reason"] == "no request intent"
    
    # Auto mode with trigger word "food"
    resp = client.post("/recommend", json={"channel_id": "ch1", "message": "I want food", "mode": "auto"})
    data = resp.json()
    # It passes intent gate, but sink is empty so it returns "no relevant match"
    assert data["suppressed"] is True
    assert data["reason"] == "no relevant match"

def test_recommend_spam_guard_cooldown_and_dedup():
    sink = StubChromaSink()
    sink.set_results([
        {
            "metadata": {"content_hash": "a1", "venue_name": "Pub"},
            "distance": 0.1
        }
    ])
    settings = IngestionSettings(rec_cooldown_s=10, rec_max_distance=0.5, rec_max_results=3)
    service = RecommendationService(sink, settings)
    
    # First auto request
    res = service.recommend("ch1", "food", mode="auto", now=100.0)
    assert not res.suppressed
    
    # Second auto request immediately -> cooldown
    res = service.recommend("ch1", "food", mode="auto", now=105.0)
    assert res.suppressed
    assert res.reason == "cooldown"
    
    # Command request bypasses cooldown but gets deduped because 'a1' was just suggested
    res = service.recommend("ch1", "food", mode="command", now=105.0)
    assert res.suppressed
    assert res.reason == "no relevant match"
    
    # After dedup window, it should be suggested again
    settings.rec_dedup_window_s = 60
    res = service.recommend("ch1", "food", mode="command", now=200.0)
    assert not res.suppressed


def test_apply_vote_identity_and_anonymous():
    from src.ingestion.serving.admin import VoteBody, _apply_vote, _voters

    meta: dict = {}
    _apply_vote(meta, VoteBody(delta=1, user_id="1", user_name="nick"))
    _apply_vote(meta, VoteBody(delta=1, user_id="2", user_name="sam"))
    _apply_vote(meta, VoteBody(delta=1, user_id="1", user_name="nick"))  # idempotent re-vote
    assert meta["votes"] == 2
    assert set(_voters(meta).values()) == {"nick", "sam"}

    _apply_vote(meta, VoteBody(delta=-1, user_id="2"))   # "Not for me" leaves the list
    assert meta["votes"] == 1
    assert set(_voters(meta).values()) == {"nick"}

    anon = _apply_vote({}, VoteBody(delta=1))            # web UI keeps the plain counter
    assert anon["votes"] == 1
    assert "voters" not in anon


def test_recommend_routes_by_guild(monkeypatch):
    """Each guild_id gets its own service/collection; '' reuses the legacy one."""
    built = []
    settings = IngestionSettings(rec_max_distance=0.5)

    def fake_build(guild_id=""):
        built.append(guild_id)
        return RecommendationService(StubChromaSink(), settings)

    monkeypatch.setattr(serving_app, "_build_service", fake_build)
    monkeypatch.setattr(serving_app, "_service", None)
    monkeypatch.setattr(serving_app, "_services", {})

    client = TestClient(app)
    base = {"channel_id": "ch1", "message": "tacos", "mode": "command"}
    client.post("/recommend", json={**base, "guild_id": "g1"})
    client.post("/recommend", json={**base, "guild_id": "g1"})   # cached, not rebuilt
    client.post("/recommend", json={**base, "guild_id": "g2"})
    client.post("/recommend", json=base)                          # legacy catalog
    assert built == ["g1", "g2", ""]
