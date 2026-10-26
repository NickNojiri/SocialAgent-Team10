"""Offline tests for the server's "not a place" list (serving/gists.py) and its admin endpoints:
saving, expiry and its /setup switch, the "Not a place" move and its exact undo, and deletion."""

import time

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
from src.ingestion.serving.gists import EXPIRY_DAYS, MAX_PER_SERVER, GistError, GistStore, gist_id
from src.ingestion.serving.guild_settings import GuildSettingsStore, SettingsError
from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token

GUILD = "111111111111111111"
ME = "user-me"
URL = "https://www.instagram.com/reel/DenJ7IAIX2k/"
GIST = {"title": "I Married the Girl Who Broke My Heart", "kind": "story",
        "summary": "He tells how they found their way back.", "author": "darianthao", "image_url": None}


class TestStore:
    def test_add_then_list_newest_first_and_one_entry_per_reel(self, tmp_path):
        store = GistStore(tmp_path)
        store.add(GUILD, URL, GIST)
        store.add(GUILD, "https://x.test/2", {**GIST, "title": "Second One"})
        store.add(GUILD, URL, {**GIST, "title": "Refreshed Title"})
        rows = store.list(GUILD)
        assert [r["title"] for r in rows] == ["Refreshed Title", "Second One"]
        assert rows[0]["id"] == gist_id(URL) and rows[0]["moved"] is False

    def test_old_gists_expire_unless_the_server_turned_it_off(self, tmp_path):
        store = GistStore(tmp_path)
        store.add(GUILD, URL, GIST)
        later = time.time() + (EXPIRY_DAYS + 1) * 86400
        assert store.list(GUILD, expire=False, now=later) != []
        assert store.list(GUILD, expire=True, now=later) == []
        assert store.list(GUILD, expire=False) == []            # expired ones are gone for good

    def test_the_list_is_capped(self, tmp_path):
        store = GistStore(tmp_path)
        for i in range(MAX_PER_SERVER + 5):
            store.add(GUILD, f"https://x.test/{i}", GIST)
        assert len(store.list(GUILD)) == MAX_PER_SERVER

    def test_pop_delete_and_bad_server_ids(self, tmp_path):
        store = GistStore(tmp_path)
        store.add(GUILD, URL, GIST, spot={"id": "s1"})
        assert store.pop(GUILD, gist_id(URL))["spot"] == {"id": "s1"}
        assert store.pop(GUILD, gist_id(URL)) is None
        store.add(GUILD, URL, GIST)
        assert store.delete(GUILD) == 1 and store.list(GUILD) == []
        with pytest.raises(GistError):
            store.add("../etc", URL, GIST)

    def test_a_damaged_file_reads_as_empty(self, tmp_path):
        store = GistStore(tmp_path)
        store.path_for(GUILD).write_text("{not json", encoding="utf-8")
        assert store.list(GUILD) == []


class TestExpirySetting:
    def test_on_by_default_and_must_be_a_real_bool(self, tmp_path):
        settings = GuildSettingsStore(tmp_path)
        assert settings.get(GUILD)["gists_expire"] is True
        assert settings.update(GUILD, {"gists_expire": False})["gists_expire"] is False
        with pytest.raises(SettingsError):
            settings.update(GUILD, {"gists_expire": "no"})


class Collection:
    """A one-spot Chroma stand-in that remembers what was deleted and added back."""

    def __init__(self):
        self.rows = {"spot1": ({"venue_name": "Not Really A Place", "category": "other", "votes": 2,
                                "voters": '{"u1": "Ana"}', "source_url": URL, "summary": "A wedding toast.",
                                "image_url": "https://cdn.ig/t.jpg"}, "doc text", [0.5, 0.25])}

    def get(self, ids=None, include=None):
        hit = [i for i in (ids or self.rows) if i in self.rows]
        return {"ids": hit, "metadatas": [self.rows[i][0] for i in hit],
                "documents": [self.rows[i][1] for i in hit], "embeddings": [self.rows[i][2] for i in hit]}

    def delete(self, ids=None):
        for i in ids:
            self.rows.pop(i, None)

    def add(self, ids, metadatas, documents, embeddings=None):
        self.rows[ids[0]] = (metadatas[0], documents[0], embeddings[0] if embeddings else None)


class Sink:
    def __init__(self):
        self.collection = Collection()

    def add(self, record):
        return "manual-id"


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, "0" * 64)
    sink = Sink()
    monkeypatch.setattr(admin, "_sink_for", lambda guild_id="", **_: sink)
    monkeypatch.setattr(admin, "_gists", GistStore(tmp_path / "gists"))
    monkeypatch.setattr(admin, "_guild_settings", GuildSettingsStore(tmp_path / "settings"))
    monkeypatch.setattr(admin, "_TAPS", tmp_path / "taps.jsonl")
    monkeypatch.setattr(admin, "describe_post", lambda *a, **k: {
        "title": "Wedding Toast Gone Wrong", "kind": "story", "summary": "The toast goes wrong."})
    with TestClient(admin.app) as client:
        yield client, sink, tmp_path


def _as_me():
    return {"X-Tenant-Token": mint_token(GUILD, user_id=ME)}


def test_not_a_place_moves_the_spot_and_move_back_restores_it_exactly(api):
    client, sink, tmp_path = api
    before = sink.collection.rows["spot1"]
    r = client.post(f"/api/events/spot1/not-a-place?guild_id={GUILD}", json={"user_id": ME}, headers=_as_me())
    assert r.status_code == 200 and r.json()["title"] == "Wedding Toast Gone Wrong" and "spot" not in r.json()
    assert "spot1" not in sink.collection.rows

    listed = client.get(f"/api/gists?guild_id={GUILD}", headers={"X-Tenant-Token": mint_token(GUILD)}).json()
    assert [g["title"] for g in listed["gists"]] == ["Wedding Toast Gone Wrong"] and listed["gists"][0]["moved"]

    back = client.post(f"/api/gists/{gist_id(URL)}/restore?guild_id={GUILD}", json={"user_id": ME}, headers=_as_me())
    assert back.status_code == 200 and back.json()["votes"] == 2 and back.json()["voters"] == ["Ana"]
    assert sink.collection.rows["spot1"] == before                 # same metadata, text and embedding
    taps = (tmp_path / "taps.jsonl").read_text(encoding="utf-8").split("\n")
    assert '"not_a_place": true' in taps[0] and '"not_a_place": false' in taps[1]
    assert ME not in "".join(taps)                                  # labels carry no user id


def test_a_pasted_reel_cant_be_restored_as_a_spot_and_stays_listed(api):
    client, _, _ = api
    admin._gists.add(GUILD, URL, GIST)
    r = client.post(f"/api/gists/{gist_id(URL)}/restore?guild_id={GUILD}", json={"user_id": ME}, headers=_as_me())
    assert r.status_code == 409 and len(admin._gists.list(GUILD)) == 1


def test_adding_it_anyway_takes_it_off_the_list(api):
    client, _, _ = api
    admin._gists.add(GUILD, URL, GIST)
    r = client.post("/api/manual", json={"venue": "Wedding Venue", "theme": "where they married",
                                         "source_url": URL, "guild_id": GUILD},
                    headers={"X-Tenant-Token": mint_token(GUILD)})
    assert r.status_code == 200 and admin._gists.list(GUILD) == []


def test_the_expiry_switch_is_saved_through_settings(api):
    client, _, _ = api
    token = {"X-Tenant-Token": mint_token(GUILD)}
    assert client.put("/api/settings", json={"guild_id": GUILD, "gists_expire": False}, headers=token).status_code == 200
    assert client.get(f"/api/gists?guild_id={GUILD}", headers=token).json()["expire"] is False
