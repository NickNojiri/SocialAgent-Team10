"""Per-server settings behind the /setup wizard (Track C #8).

The store keeps one file per server; the API changes only the fields it is sent.
The test that matters most for #8: re-running /setup never touches a server's
catalog or the settings it didn't change.
"""

import json

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
from src.ingestion.config import IngestionSettings
from src.ingestion.serving.guild_settings import (
    DEFAULTS,
    MAX_CITY_LEN,
    GuildSettingsStore,
    SettingsError,
)
from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token
from src.ingestion.sinks.chroma_sink import ChromaSink

GUILD = "111111111111111111"


# ── the store ───────────────────────────────────────────────────────────────


def test_an_unconfigured_server_gets_the_defaults(tmp_path):
    store = GuildSettingsStore(tmp_path)
    assert store.get(GUILD) == DEFAULTS
    assert not any(tmp_path.iterdir())                  # reading writes nothing


def test_update_changes_only_what_it_is_given(tmp_path):
    store = GuildSettingsStore(tmp_path)
    store.update(GUILD, {"drop_channel_id": "42", "home_city": "Long Beach, CA"})
    after = store.update(GUILD, {"home_city": "  Fullerton,   CA "})
    assert after["drop_channel_id"] == "42"             # untouched
    assert after["home_city"] == "Fullerton, CA"        # whitespace collapsed
    assert after["updated_at"] > 0
    assert store.get(GUILD) == after
    assert store.update(GUILD, {"home_city": "Long\nBeach"})["home_city"] == "Long Beach"


def test_clearing_the_drop_channel_means_every_channel(tmp_path):
    store = GuildSettingsStore(tmp_path)
    store.update(GUILD, {"drop_channel_id": "42"})
    assert store.update(GUILD, {"drop_channel_id": None})["drop_channel_id"] is None


def test_each_server_has_its_own_file(tmp_path):
    store = GuildSettingsStore(tmp_path)
    store.update(GUILD, {"home_city": "Long Beach"})
    store.update("dm-7", {"home_city": "Irvine"})
    assert store.get(GUILD)["home_city"] == "Long Beach"
    assert store.get("dm-7")["home_city"] == "Irvine"
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{GUILD}.json", "dm-7.json"]


def test_non_ascii_cities_round_trip_as_utf8(tmp_path):
    store = GuildSettingsStore(tmp_path)
    store.update(GUILD, {"home_city": "São Paulo"})
    raw = (tmp_path / f"{GUILD}.json").read_text(encoding="utf-8")
    assert "São Paulo" in raw and store.get(GUILD)["home_city"] == "São Paulo"


def test_a_save_leaves_no_temp_files_behind(tmp_path):
    store = GuildSettingsStore(tmp_path)
    for city in ("A", "B", "C"):
        store.update(GUILD, {"home_city": city})
    assert [p.name for p in tmp_path.iterdir()] == [f"{GUILD}.json"]


def test_a_damaged_file_reads_as_unconfigured_not_a_crash(tmp_path):
    (tmp_path / f"{GUILD}.json").write_text("{not json", encoding="utf-8")
    assert GuildSettingsStore(tmp_path).get(GUILD) == DEFAULTS


def test_delete_forgets_one_server_only(tmp_path):
    store = GuildSettingsStore(tmp_path)
    store.update(GUILD, {"home_city": "Long Beach"})
    store.update("222", {"home_city": "Irvine"})
    assert store.delete(GUILD) is True
    assert store.delete(GUILD) is False
    assert store.get(GUILD) == DEFAULTS and store.get("222")["home_city"] == "Irvine"


@pytest.mark.parametrize("bad", ["", "../etc/passwd", "a b", "1.2", "x" * 65, "g/1"])
def test_a_guild_id_that_could_share_or_escape_a_file_is_refused(tmp_path, bad):
    with pytest.raises(SettingsError):
        GuildSettingsStore(tmp_path).update(bad, {"home_city": "x"})


@pytest.mark.parametrize("changes", [
    {"home_city": "x" * (MAX_CITY_LEN + 1)},
    {"home_city": "Long\x07Beach"},
    {"drop_channel_id": "general"},
    {"drop_channel_id": "12345678901234567890123"},
])
def test_values_the_wizard_never_sends_are_refused(tmp_path, changes):
    with pytest.raises(SettingsError):
        GuildSettingsStore(tmp_path).update(GUILD, changes)


# ── the API ─────────────────────────────────────────────────────────────────


def _embed(texts):
    return [[float(len(t) % 7) for _ in range(8)] for t in texts]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0" * 64)
    monkeypatch.setattr(admin, "_guild_settings", GuildSettingsStore(tmp_path / "settings"))
    sink = ChromaSink(IngestionSettings(chroma_path=str(tmp_path / "chroma")), embedder=_embed)
    monkeypatch.setattr(admin, "_sink_for", lambda guild_id="": sink)
    headers = {"X-Tenant-Token": mint_token(GUILD)}
    with TestClient(admin.app) as client:
        yield client, headers, sink


def test_rerunning_setup_never_touches_the_catalog(api):
    client, headers, sink = api
    added = client.post("/api/manual", json={"guild_id": GUILD, "venue": "Casa Loma",
                                             "theme": "birria tacos"}, headers=headers)
    assert added.status_code == 200
    before = sink.collection.get(include=["metadatas"])

    first = client.put("/api/settings", json={"guild_id": GUILD, "drop_channel_id": "42",
                                              "home_city": "Long Beach, CA"}, headers=headers)
    again = client.put("/api/settings", json={"guild_id": GUILD, "home_city": "Fullerton, CA"},
                       headers=headers)
    assert first.status_code == again.status_code == 200

    assert sink.collection.get(include=["metadatas"]) == before     # spots, votes: identical
    settings = client.get(f"/api/settings?guild_id={GUILD}", headers=headers).json()["settings"]
    assert settings["drop_channel_id"] == "42"                     # the field not re-sent survives
    assert settings["home_city"] == "Fullerton, CA"


def test_sending_null_clears_but_leaving_a_field_out_keeps_it(api):
    client, headers, _ = api
    client.put("/api/settings", json={"guild_id": GUILD, "drop_channel_id": "42"}, headers=headers)
    kept = client.put("/api/settings", json={"guild_id": GUILD, "home_city": "x"}, headers=headers)
    assert kept.json()["settings"]["drop_channel_id"] == "42"
    cleared = client.put("/api/settings", json={"guild_id": GUILD, "drop_channel_id": None},
                         headers=headers)
    assert cleared.json()["settings"]["drop_channel_id"] is None


def test_bad_values_are_a_400_with_a_reason(api):
    client, headers, _ = api
    r = client.put("/api/settings", json={"guild_id": GUILD, "home_city": "x" * 200},
                   headers=headers)
    assert r.status_code == 400 and "longer than" in r.json()["detail"]
    r = client.put("/api/settings", json={"guild_id": GUILD, "drop_channel_id": "general"},
                   headers=headers)
    assert r.status_code == 400


def test_the_legacy_catalog_has_no_server_settings(api):
    client, _, _ = api
    assert client.get("/api/settings").status_code == 400
    assert client.put("/api/settings", json={"guild_id": "", "home_city": "x"}).status_code == 400


def test_nothing_but_the_three_fields_is_stored(api, tmp_path):
    client, headers, _ = api
    client.put("/api/settings", json={"guild_id": GUILD, "home_city": "x", "user_id": "u1",
                                      "token": "t"}, headers=headers)
    saved = json.loads((tmp_path / "settings" / f"{GUILD}.json").read_text(encoding="utf-8"))
    assert set(saved) == set(DEFAULTS)
