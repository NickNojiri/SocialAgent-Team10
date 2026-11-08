"""scripts/eval_report.py (Track B #17) and scripts/category_classifier.py (Track B #16)."""
import importlib.util
import sys
from pathlib import Path

import pytest


def _load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rep = _load("eval_report")
cc = _load("category_classifier")


def test_report_is_one_page_with_intervals_charts_and_errors(tmp_path):
    out = tmp_path / "r.html"
    rep.main(["--split", "dev", "--out", str(out)])
    page = out.read_text(encoding="utf-8")
    for part in ("95% interval (Wilson)", "<svg", "Category confusion", "Venues missed", "Categories missed",
                 "python scripts/eval_report.py --split dev"):
        assert part in page
    assert page.count('class="ci"') >= 5                       # a whisker on every bar with data


def test_report_never_offers_the_test_split():
    with pytest.raises(SystemExit):
        rep.main(["--split", "test"])


def test_centroids_pick_the_nearest_kind():
    cents = cc.centroids([[1, 0], [0.9, 0.1], [0, 1]], ["food_drink", "food_drink", "nightlife"])
    assert cc.predict(cents, [1, 0.2]) == "food_drink" and cc.predict(cents, [0.1, 1]) == "nightlife"


def test_hybrid_only_overrides_rules_that_said_other(monkeypatch):
    rows = [{"url": f"https://www.instagram.com/reel/T{i}/", "input": {"caption": c}, "split": s,
             "gold": {"category": g}, "verdict": {"category": "right"}}
            for i, (c, g, s) in enumerate([("ramen noodles", "food_drink", "train"), ("cocktail bar", "nightlife", "train"),
                                            ("ramen", "food_drink", "dev"), ("cocktail", "nightlife", "dev")])]
    monkeypatch.setattr(cc.ev, "split_of", lambda r: r["split"])
    monkeypatch.setattr(cc, "rules_category", lambda r: "food_drink" if "ramen" in r["input"]["caption"] else "other")
    res = cc.run(rows, cc.hashed_embedder(), "hybrid")
    assert (res["train"], res["dev"], res["rules"], res["chosen"]) == (2, 2, 1, 2)
    assert "no proven gain" in cc.report(res, "hashed")          # 2 reels can't prove anything


def test_embeddings_are_cached(tmp_path):
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[float(len(t))] for t in texts]

    run = cc.cached(embed, "m", tmp_path / "c.json")
    assert run(["a", "bb"]) == [[1.0], [2.0]] and run(["bb", "a"]) == [[2.0], [1.0]]
    assert calls == [["a", "bb"]]
