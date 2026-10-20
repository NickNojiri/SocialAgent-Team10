"""experiments/fit_check.py and scripts/count_captures.py."""

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fc = _load("fit_check", "experiments/fit_check.py")
cc = _load("count_captures", "scripts/count_captures.py")


def test_a_big_real_gap_is_overfitting_and_a_small_one_is_not():
    assert fc.verdict(fc.Rate(240, 300), fc.Rate(60, 100)).startswith("OVERFITTING")
    assert fc.verdict(fc.Rate(240, 300), fc.Rate(78, 100)).startswith("fits")
    assert "within noise" in fc.verdict(fc.Rate(28, 40), fc.Rate(22, 40))


def test_far_under_the_ceiling_is_underfitting():
    v = fc.verdict(fc.Rate(150, 300), fc.Rate(48, 100), ceiling=0.94)
    assert "UNDERFITTING" in v and "OVERFITTING" not in v
    assert fc.verdict(fc.Rate(270, 300), fc.Rate(88, 100), ceiling=0.94).startswith("fits")


def test_small_samples_are_not_judged():
    assert fc.verdict(fc.Rate(10, 14), fc.Rate(2, 14)) == "too few reels to judge"


def test_ceiling_counts_gold_names_present_in_the_text():
    rows = [{"gold": {"venue": "Tacos Manny"}, "input": {"caption": "go to @tacosmanny"}},
            {"gold": {"venue": "Hidden Spot"}, "input": {"caption": "so good"}},
            {"gold": {"venue": None}, "input": {"caption": "a recipe"}}]
    assert fc.ceiling(rows) == 0.5


def test_counter_counts_unlabeled_reels_and_prints_nothing_private(tmp_path, capsys, monkeypatch):
    (tmp_path / "data" / "inspirations").mkdir(parents=True)
    (tmp_path / "fixtures").mkdir()
    labels = tmp_path / "fixtures" / "labels.jsonl"
    labels.write_text(json.dumps({"url": "https://www.instagram.com/reel/OLD1/"}) + "\n", encoding="utf-8")
    secret_caption = "private caption text"
    capture = {"venue_name": "X", "core_theme": secret_caption,
               "provenance": {"source_url": "https://www.instagram.com/reel/NEW1/"}}
    old = {"provenance": {"source_url": "https://www.instagram.com/reel/OLD1/"}}
    (tmp_path / "data" / "inspirations" / "123456789012345678.jsonl").write_text(
        json.dumps(capture) + "\n" + json.dumps(old) + "\n", encoding="utf-8")
    (tmp_path / "fixtures" / "labels.inbox.jsonl").write_text(
        json.dumps({"url": "https://www.tiktok.com/@a/video/NEW2", "input": {"caption": secret_caption}})
        + "\n" + json.dumps({"url": "https://www.instagram.com/reel/NEW1/"}) + "\nnot json\n", encoding="utf-8")

    c = cc.count(tmp_path, labels)
    assert c["labeled"] == 1 and c["unlabeled_unique"] == 2
    assert c["unlabeled_by_platform"] == {"instagram": 1, "tiktok": 1}
    assert sum(c["would_land_in"].values()) == 2

    monkeypatch.setattr(cc, "REPO", tmp_path)
    monkeypatch.setattr(cc, "LABELS_PATH", labels)
    cc.main([])
    cc.main(["--json"])
    out = capsys.readouterr().out
    for private in ("NEW1", "NEW2", "OLD1", "123456789012345678", secret_caption, "instagram.com/"):
        assert private not in out


bv = _load("build_venue_list", "scripts/build_venue_list.py")


def test_venue_list_keeps_every_name_once_and_drops_junk():
    data = {"elements": [
        {"tags": {"name": "Tacos Manny", "amenity": "restaurant", "brand": "Tacos Manny"}},
        {"tags": {"name": "tacos manny"}},                                   # same name, other case
        {"tags": {"name": "Blue Bottle Coffee;Blue Bottle", "alt_name": "BB"}},
        {"tags": {"amenity": "cafe"}},                                       # no name
    ]}
    assert bv.names(data) == ["Blue Bottle", "Blue Bottle Coffee", "Tacos Manny"]
    q = bv.query()
    assert "restaurant" in q and "33.4,-118.95,34.35,-117.4" in q and "out tags" in q


def test_venue_list_moves_to_the_next_server_only_when_busy():
    import io
    import urllib.error
    tried = []

    def fake_open(req, timeout):
        tried.append(req.full_url)
        if len(tried) == 1:
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)
        return io.BytesIO(b'{"elements": [{"tags": {"name": "Tacos Manny"}}]}')

    data = bv.fetch("q", endpoints=("https://a/api", "https://b/api", "https://c/api"), pause=0, open_url=fake_open)
    assert bv.names(data) == ["Tacos Manny"] and tried == ["https://a/api", "https://b/api"]

    def not_busy(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

    import pytest
    with pytest.raises(urllib.error.HTTPError):
        bv.fetch("q", endpoints=("https://a/api", "https://b/api"), pause=0, open_url=not_busy)
