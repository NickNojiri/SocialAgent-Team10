"""Deleting a server's data (Track C #21, threat model T6).

The #21 "done when": create a test server's data, delete it, and nothing comes
back — from any endpoint, or from any file. Every store is real here (Chroma,
the SQLite job store, the capture log, the settings files, the capture JSONL and
snapshots); only the capture itself is faked. A second server's data must
survive untouched.
"""

import asyncio
import json
import time
from collections import deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
import src.ingestion.serving.app as serving_app
from src.ingestion.serving import forget as data_purge
from src.ingestion.serving.capture_limits import CaptureRateLimiter
from src.ingestion.serving.feedback import FeedbackStore
from src.ingestion.serving.guild_settings import DEFAULTS, GuildSettingsStore
from src.ingestion.serving.jobs import JobQueue, SqliteJobStore
from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token
from src.ingestion.sinks.chroma_sink import ChromaSink

A, B = "111111111111111111", "222222222222222222"
SECRETS = {
    "url": "https://www.instagram.com/reel/SECRETPOST123/",
    "captured": "Hidden Taqueria",
    "manual": "Secret Supper Club",
    "voter": "Voter McSecretface",
    "city": "Secretville, CA",
}
SHARED = ("Both Servers Bakery", "croissants and coffee")
OTHER_URL = "https://www.instagram.com/reel/OTHERPOST/"


def _embed(texts):
    return [[float(len(t) % 7) + 0.1 for _ in range(8)] for t in texts]


async def _fake_capture(urls, guild_id, on_stage):
    """Writes where a real capture writes: the catalog, the server's capture file,
    a failed-page snapshot, and the in-memory activity feed."""
    key = data_purge.guild_file_key(guild_id)
    venue = SECRETS["captured"] if urls[0] == SECRETS["url"] else "Other Taqueria"
    record = admin._manual_record(venue, "al pastor at midnight", urls[0])
    event_id = admin._sink_for(guild_id).add(record)
    admin._JSONL_DIR.mkdir(parents=True, exist_ok=True)
    with (admin._JSONL_DIR / f"{key}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")
    raw = admin._RAW_ROOT / key
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "snapshot-1.html").write_text(f"<html>{urls[0]}</html>", encoding="utf-8")
    admin._CAPTURE_LOG.appendleft({"ts": 1, "guild_id": guild_id, "urls": 1, "added": 1,
                                   "lines": [f"[ok] {urls[0]} -> {venue}"]})
    return {"added": 1, "events": [{"id": event_id, "venue": venue}]}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0" * 64)
    data = tmp_path / "data"
    monkeypatch.setattr(admin, "_settings", admin._settings.model_copy(update={"chroma_path": str(data)}))
    monkeypatch.setattr(admin, "_sinks", {})
    monkeypatch.setattr(admin, "ChromaSink",
                        lambda s, collection_name=None: ChromaSink(s, embedder=_embed,
                                                                   collection_name=collection_name))
    monkeypatch.setattr(admin, "_guild_settings", GuildSettingsStore(data / "guild_settings"))
    monkeypatch.setattr(admin, "_feedback", FeedbackStore(data / "feedback"))
    monkeypatch.setattr(admin, "_JSONL_DIR", data / "inspirations")
    monkeypatch.setattr(admin, "_LEGACY_JSONL", data / "inspirations.jsonl")
    monkeypatch.setattr(admin, "_RAW_ROOT", data / "raw")
    monkeypatch.setattr(admin, "_CAPTURE_LOG", deque(maxlen=50))
    monkeypatch.setattr(admin, "_capture_limits", CaptureRateLimiter(user_limit=100))
    store = SqliteJobStore(data / "jobs.db")
    monkeypatch.setattr(admin, "_jobs", JobQueue(_fake_capture, log_path=data / "capture_jobs.jsonl",
                                                 store=store, backoff_base_s=0))
    with TestClient(admin.app) as client:
        yield client, data
    store.close()


def _hdr(guild, user=""):
    return {"X-Tenant-Token": mint_token(guild, user_id=user)}


def _seed(client, guild, venue, voter, city, url=SECRETS["url"]):
    """A server with a captured spot (via the job queue), a manual spot with a
    named vote, the shared spot, and /setup settings."""
    r = client.post("/api/jobs", json={"urls": [url], "guild_id": guild, "user_id": "u1"},
                    headers=_hdr(guild, "u1"))
    job_id = r.json()["job_id"]
    for _ in range(200):
        if client.get(f"/api/jobs/{job_id}", headers=_hdr(guild)).json()["state"] == "done":
            break
        time.sleep(0.02)
    spot = client.post("/api/manual", json={"guild_id": guild, "venue": venue, "theme": "tasting menu"},
                       headers=_hdr(guild)).json()
    client.post(f"/api/events/{spot['id']}/vote?guild_id={guild}",
                json={"delta": 1, "user_id": "u9", "user_name": voter}, headers=_hdr(guild, "u9"))
    client.post("/api/manual", json={"guild_id": guild, "venue": SHARED[0], "theme": SHARED[1]},
                headers=_hdr(guild))
    client.put("/api/settings", json={"guild_id": guild, "home_city": city}, headers=_hdr(guild))
    client.post("/api/feedback", json={"guild_id": guild, "kind": "bug",
                                       "text": f"the card for {venue} was wrong"}, headers=_hdr(guild))
    client.post("/api/survey", json={"guild_id": guild, "answers": [4, 2] * 5,
                                     "participant": "P1"}, headers=_hdr(guild))
    return job_id, spot["id"]


def _legacy_row(content_hash, venue):
    return json.dumps({"venue_name": venue, "provenance": {"content_hash": content_hash}})


def _files_containing(root: Path, needles) -> dict:
    hits = {}
    for p in root.rglob("*"):
        if p.is_file():
            blob = p.read_bytes()
            found = [n for n in needles if n.encode("utf-8") in blob]
            if found:
                hits[str(p.relative_to(root))] = found
    return hits


def test_deleting_a_server_leaves_nothing_behind(world):
    client, data = world
    job_a, spot_a = _seed(client, A, SECRETS["manual"], SECRETS["voter"], SECRETS["city"])
    _seed(client, B, "Other Place", "Other Voter", "Long Beach, CA", url=OTHER_URL)
    shared_id = admin._manual_record(*SHARED).provenance.content_hash
    # The pre-#21 shared file: one row only A had, one both servers had.
    (data / "inspirations.jsonl").write_text(
        _legacy_row(spot_a, SECRETS["manual"]) + "\n" + _legacy_row(shared_id, SHARED[0]) + "\n",
        encoding="utf-8")
    assert _files_containing(data, SECRETS.values())         # the data is really there first

    r = client.post("/api/forget", json={"guild_id": A, "confirm": A}, headers=_hdr(A))
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["catalog"]["spots"] == 3 and report["catalog"]["vacuumed"] is True, report
    assert report["catalog"]["shared_file_rows"] == 1, report
    assert report["catalog"]["shared_posts_kept"] == 1, report
    assert report["settings"] is True and report["jobs"]["stored_jobs"] == 1, report
    assert report["jobs"]["log_lines"] == 1 and report["activity_entries"] == 1, report
    assert report["feedback_rows"] == 2, report

    # Nothing comes back from any endpoint...
    assert client.get(f"/api/events?guild_id={A}", headers=_hdr(A)).json()["events"] == []
    assert client.get(f"/api/nights?guild_id={A}", headers=_hdr(A)).json()["nights"] == 0
    assert client.get(f"/api/settings?guild_id={A}", headers=_hdr(A)).json()["settings"] == DEFAULTS
    assert client.get(f"/api/jobs?guild_id={A}", headers=_hdr(A)).json()["jobs"] == []
    assert client.get(f"/api/jobs/{job_a}", headers=_hdr(A)).status_code == 404
    assert client.get(f"/api/survey?guild_id={A}", headers=_hdr(A)).json()["responses"] == 0
    assert A not in client.get("/api/stats").text

    # ...or from any file: not a venue, a voter's name, the city, the link, or the server id.
    leftovers = _files_containing(data, [*SECRETS.values(), "SECRETPOST123", A])
    assert leftovers == {}, f"server A's data survived in {leftovers}"

    # The other server is untouched, including the post both servers saved.
    venues = {e["venue"] for e in client.get(f"/api/events?guild_id={B}", headers=_hdr(B)).json()["events"]}
    assert {"Other Place", SHARED[0], "Other Taqueria"} <= venues
    assert client.get(f"/api/settings?guild_id={B}", headers=_hdr(B)).json()["settings"]["home_city"] == "Long Beach, CA"
    assert SHARED[0] in (data / "inspirations.jsonl").read_text(encoding="utf-8")
    assert (data / "inspirations" / f"{B}.jsonl").exists()


def test_a_deleted_server_can_start_over(world):
    client, _ = world
    _seed(client, A, SECRETS["manual"], SECRETS["voter"], SECRETS["city"])
    client.post("/api/forget", json={"guild_id": A, "confirm": A}, headers=_hdr(A))
    spot = client.post("/api/manual", json={"guild_id": A, "venue": "Fresh Start", "theme": "new era"},
                       headers=_hdr(A))
    assert spot.status_code == 200
    events = client.get(f"/api/events?guild_id={A}", headers=_hdr(A)).json()["events"]
    assert [e["venue"] for e in events] == ["Fresh Start"]


def test_confirm_must_repeat_the_server_id(world):
    client, _ = world
    _seed(client, A, SECRETS["manual"], SECRETS["voter"], SECRETS["city"])
    for confirm in ("", "yes", B):
        r = client.post("/api/forget", json={"guild_id": A, "confirm": confirm}, headers=_hdr(A))
        assert r.status_code == 400
    assert client.get(f"/api/events?guild_id={A}", headers=_hdr(A)).json()["events"]


def test_the_shared_catalog_cant_be_deleted_this_way(world):
    client, _ = world
    assert client.post("/api/forget", json={"guild_id": "", "confirm": ""}).status_code == 400


def test_refused_while_a_capture_is_running(world, monkeypatch):
    client, _ = world
    gate = asyncio.Event()

    async def stuck(urls, guild_id, on_stage):
        await gate.wait()
        return {"added": 0, "events": []}

    monkeypatch.setattr(admin, "_jobs", JobQueue(stuck))
    client.post("/api/jobs", json={"urls": [SECRETS["url"]], "guild_id": A, "user_id": "u1"},
                headers=_hdr(A, "u1"))
    r = client.post("/api/forget", json={"guild_id": A, "confirm": A}, headers=_hdr(A))
    assert r.status_code == 409


def test_refused_while_a_sync_capture_is_running(world, monkeypatch):
    client, _ = world
    monkeypatch.setitem(admin._capturing, A, 1)
    r = client.post("/api/forget", json={"guild_id": A, "confirm": A}, headers=_hdr(A))
    assert r.status_code == 409


# ── leftovers a process couldn't remove ──────────────────────────────────────


def test_finish_pending_removes_only_the_folders_it_named(tmp_path):
    index = tmp_path / "0f0e0d0c-0b0a-4908-8706-050403020100"
    index.mkdir()
    (index / "data_level0.bin").write_bytes(b"\x00" * 16)
    keep = tmp_path / "keep-me"
    keep.mkdir()
    (tmp_path / data_purge.PENDING_FILE).write_text(
        json.dumps({"dirs": [index.name, "keep-me", "../escape"], "vacuum": False}), encoding="utf-8")

    assert data_purge.finish_pending(tmp_path) == {"removed": 1, "left": 0}
    assert not index.exists() and keep.exists()
    assert not (tmp_path / data_purge.PENDING_FILE).exists()
    assert data_purge.finish_pending(tmp_path) == {"removed": 0, "left": 0}   # no marker: no-op


def test_the_recommend_service_rebuilds_a_deleted_catalog(tmp_path, monkeypatch):
    """It caches a collection handle per server; after /api/forget, Chroma fails
    every call on that handle, so the service must notice and rebuild."""
    monkeypatch.setattr(serving_app, "_settings",
                        serving_app._settings.model_copy(update={"chroma_path": str(tmp_path)}))
    monkeypatch.setattr(serving_app, "_services", {})
    first = serving_app.get_service(A)
    first.sink.collection.add(ids=["x"], documents=["d"], embeddings=[[0.1] * 8])
    data_purge.purge_catalog(tmp_path, first.sink.collection.name)

    again = serving_app.get_service(A)
    assert again is not first and again.sink.collection.count() == 0
