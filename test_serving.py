import pytest
from fastapi.testclient import TestClient
from src.ingestion.serving.app import app
import src.ingestion.serving.app as serving_app

class StubSink:
    def __init__(self, throw=False, count_val=42):
        self._throw = throw
        self._count = count_val

    def count(self):
        if self._throw:
            raise RuntimeError("chroma is down")
        return self._count

class StubService:
    def __init__(self, throw=False, count_val=42):
        self.sink = StubSink(throw=throw, count_val=count_val)

def test_ready_success(monkeypatch):
    monkeypatch.setattr(serving_app, "_service", StubService(count_val=10))
    client = TestClient(app)
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"ready": True, "events": 10}

def test_ready_failure(monkeypatch):
    monkeypatch.setattr(serving_app, "_service", StubService(throw=True))
    client = TestClient(app)
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json() == {"ready": False, "reason": "chroma is down"}
