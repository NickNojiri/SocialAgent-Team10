"""Time-to-card (Track C #18): paste → card, as the person pasting sees it.

Done-when: you can quote today's median and p95 and watch them move — so the
numbers must be right, recent pastes must be told apart from old ones, and they
must reach /api/stats and /dash.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
from src.ingestion.serving.capture_stats import percentile
from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token
from src.ingestion.serving.time_to_card import TimeToCardLog, TimingError

GUILD = "111111111111111111"


@pytest.mark.parametrize("values, pct, expected", [
    (list(range(1, 11)), 50, 5),       # the 5th of 10 — the old rounding gave the 6th
    (list(range(1, 21)), 95, 19),      # the 19th of 20 — the old rounding gave the max
    (list(range(1, 21)), 50, 10),
    ([1, 2, 3], 50, 2),
    ([7], 95, 7),
    ([3, 1, 2], 100, 3),
    ([3, 1, 2], 0, 1),
])
def test_percentile_is_nearest_rank(values, pct, expected):
    assert percentile(values, pct) == expected


def test_rows_hold_timing_only(tmp_path):
    log = TimeToCardLog(tmp_path / "ttc.jsonl")
    log.record(41.5, "card", links=2)
    row = json.loads((tmp_path / "ttc.jsonl").read_text(encoding="utf-8"))
    assert set(row) == {"ts", "seconds", "outcome", "links"}     # no server, user or link


@pytest.mark.parametrize("seconds, outcome, links", [
    (-1, "card", 1), (3601, "card", 1), ("soon", "card", 1), (10, "maybe", 1), (10, "card", 0), (10, "card", 11),
])
def test_reports_the_bot_never_sends_are_refused(tmp_path, seconds, outcome, links):
    with pytest.raises(TimingError):
        TimeToCardLog(tmp_path / "ttc.jsonl").record(seconds, outcome, links)


def test_median_and_p95_count_only_pastes_that_became_cards(tmp_path):
    log = TimeToCardLog(tmp_path / "ttc.jsonl")
    for seconds in range(10, 110, 5):        # 20 cards: 10, 15, … 105
        log.record(seconds, "card")
    log.record(900, "no_card")                # a failure isn't a slow card
    day = log.summary()["last_24h"]
    assert day == {"pastes": 21, "cards": 20, "median_s": 55.0, "p95_s": 100.0}   # 10th and 19th of 20


def test_the_last_24_hours_move_while_all_time_remembers(tmp_path):
    path = tmp_path / "ttc.jsonl"
    old = int(time.time()) - 2 * 86400
    path.write_text("".join(json.dumps({"ts": old, "seconds": 200.0, "outcome": "card", "links": 1}) + "\n"
                            for _ in range(3)), encoding="utf-8")
    log = TimeToCardLog(path)
    log.record(20, "card")
    summary = log.summary()
    assert summary["last_24h"]["median_s"] == 20.0 and summary["last_24h"]["cards"] == 1
    assert summary["all"]["cards"] == 4 and summary["all"]["median_s"] == 200.0


def test_nothing_logged_yet_and_logging_off(tmp_path):
    assert TimeToCardLog(tmp_path / "none.jsonl").summary()["last_24h"] == {
        "pastes": 0, "cards": 0, "median_s": None, "p95_s": None}
    assert TimeToCardLog(None).summary() is None
    TimeToCardLog(None).record(5, "card")      # off: accepted, written nowhere


# ── the API and /dash ───────────────────────────────────────────────────────


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0" * 64)
    monkeypatch.setattr(admin, "_time_to_card", TimeToCardLog(tmp_path / "ttc.jsonl"))
    with TestClient(admin.app) as client:
        yield client, {"X-Tenant-Token": mint_token(GUILD)}


def test_a_report_reaches_the_ops_stats(api):
    client, headers = api
    for seconds in (30, 40, 50):
        r = client.post("/api/time-to-card", json={"guild_id": GUILD, "seconds": seconds,
                                                   "outcome": "card"}, headers=headers)
        assert r.json() == {"saved": True}
    stats = client.get("/api/stats").json()["time_to_card"]
    assert stats["last_24h"]["median_s"] == 40.0 and stats["last_24h"]["cards"] == 3
    assert GUILD not in client.get("/api/stats").text


def test_bad_reports_are_a_400(api):
    client, headers = api
    assert client.post("/api/time-to-card", json={"guild_id": GUILD, "seconds": -5, "outcome": "card"},
                       headers=headers).status_code == 400
    assert client.post("/api/time-to-card", json={"guild_id": "", "seconds": 5,
                                                  "outcome": "card"}).status_code == 400


def test_the_dash_has_a_time_to_card_panel():
    with TestClient(admin.app) as client:
        page = client.get("/dash").text
    assert 'id="ttc"' in page and "median, last 24 h" in page
