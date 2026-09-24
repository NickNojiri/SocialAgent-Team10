"""Feedback and SUS survey storage (Track C #20).

The SUS maths has to be exactly the published formula — the study reports it —
and nothing identifying is stored beyond the researcher's participant code.
"""

import json

import pytest
from fastapi.testclient import TestClient

import src.ingestion.serving.admin as admin
from src.ingestion.serving.feedback import FeedbackError, FeedbackStore, sus_score
from src.ingestion.serving.tenant_auth import ENV_VAR, mint_token

GUILD = "111111111111111111"


@pytest.mark.parametrize("answers, score", [
    ([3] * 10, 50.0),                    # neutral everywhere
    ([5, 1] * 5, 100.0),                 # agree with the good, disagree with the bad
    ([1, 5] * 5, 0.0),
    ([4, 2, 4, 1, 4, 2, 5, 2, 4, 2], 80.0),
])
def test_sus_score_is_the_published_formula(answers, score):
    assert sus_score(answers) == score


@pytest.mark.parametrize("answers", [[3] * 9, [3] * 11, [0] + [3] * 9, [6] + [3] * 9, ["3"] * 10])
def test_malformed_answers_are_refused(answers):
    with pytest.raises(FeedbackError):
        sus_score(answers)


def test_rows_carry_no_user_identity(tmp_path):
    store = FeedbackStore(tmp_path)
    store.add_feedback(GUILD, "bug", "the Retry button did nothing")
    store.add_sus(GUILD, [4, 2] * 5, participant="P3")
    rows = [json.loads(line) for line in (tmp_path / f"{GUILD}.jsonl").read_text(encoding="utf-8").split("\n") if line]
    assert [set(r) for r in rows] == [{"ts", "type", "kind", "text"},
                                      {"ts", "type", "participant", "answers", "score"}]


def test_summary_mean_and_spread(tmp_path):
    store = FeedbackStore(tmp_path)
    assert store.sus_summary(GUILD) == {"responses": 0, "mean": None, "sd": None, "scores": []}
    for answers in ([3] * 10, [5, 1] * 5, [4, 2, 4, 1, 4, 2, 5, 2, 4, 2]):
        store.add_sus(GUILD, answers)
    store.add_feedback(GUILD, "idea", "not a survey row")
    summary = store.sus_summary(GUILD)
    assert summary["responses"] == 3 and summary["mean"] == 76.7 and summary["sd"] == 25.2


@pytest.mark.parametrize("kind, text", [
    ("rant", "x"), ("bug", ""), ("bug", "   "), ("idea", "x" * 1001), ("bug", "bell\x07"),
])
def test_feedback_input_is_checked(tmp_path, kind, text):
    with pytest.raises(FeedbackError):
        FeedbackStore(tmp_path).add_feedback(GUILD, kind, text)


def test_multi_line_feedback_is_fine(tmp_path):
    store = FeedbackStore(tmp_path)
    store.add_feedback(GUILD, "bug", "steps:\n1. paste\n2. wait")
    assert store.read(GUILD)[0]["text"] == "steps:\n1. paste\n2. wait"


@pytest.mark.parametrize("code", ["P 3", "x" * 17, "<b>"])
def test_participant_codes_are_short_plain_codes(tmp_path, code):
    with pytest.raises(FeedbackError):
        FeedbackStore(tmp_path).add_sus(GUILD, [3] * 10, participant=code)


def test_each_server_has_its_own_file_and_delete_forgets_one(tmp_path):
    store = FeedbackStore(tmp_path)
    store.add_feedback(GUILD, "idea", "a")
    store.add_feedback("222", "idea", "b")
    assert store.delete(GUILD) == 1 and store.read(GUILD) == []
    assert [r["text"] for r in store.read("222")] == ["b"]


# ── the API ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "0" * 64)
    monkeypatch.setattr(admin, "_feedback", FeedbackStore(tmp_path))
    with TestClient(admin.app) as client:
        yield client, {"X-Tenant-Token": mint_token(GUILD)}


def test_a_survey_round_trip_returns_the_score(api):
    client, headers = api
    r = client.post("/api/survey", json={"guild_id": GUILD, "answers": [5, 1] * 5,
                                         "participant": "P1"}, headers=headers)
    assert r.json() == {"score": 100.0}
    summary = client.get(f"/api/survey?guild_id={GUILD}", headers=headers).json()
    assert summary["responses"] == 1 and summary["mean"] == 100.0


def test_bad_input_is_a_400(api):
    client, headers = api
    assert client.post("/api/survey", json={"guild_id": GUILD, "answers": [9] * 10},
                       headers=headers).status_code == 400
    assert client.post("/api/feedback", json={"guild_id": GUILD, "kind": "bug", "text": ""},
                       headers=headers).status_code == 400
    assert client.post("/api/feedback", json={"guild_id": "", "kind": "bug", "text": "x"}).status_code == 400
