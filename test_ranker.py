"""experiments/ranker.py — the candidate ranker experiment (Track B)."""

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("ranker", Path(__file__).parent / "experiments" / "ranker.py")
rk = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rk
_spec.loader.exec_module(rk)


def _row(caption, handle="foodiejess", **inp):
    return {"url": "https://www.instagram.com/reel/TESTCODE/", "input": {"caption": caption, "handle": handle, **inp},
            "gold": {"venue": "Tacos Manny"}, "verdict": {"venue": "right"}}


def test_candidates_collect_every_name_once_with_all_its_sources():
    row = _row("best birria 🔥 @tacosmanny went with @friendo\n📍 Tacos Manny, Boyle Heights #birria",
               location_text="Tacos Manny, Los Angeles")
    cands = {rk.sq(c.name): c for c in rk.candidates(row, ("Tacos Manny", "pin"))}
    manny = cands["tacosmanny"]
    assert {"rules", "mention", "pin", "location_tag"} <= manny.sources
    assert manny.rules_slot == "pin"
    assert "friendo" in cands and "foodiejess" in cands          # a tagged friend and the poster
    assert cands["foodiejess"].sources == {"author"}


def test_features_mark_the_poster_and_the_rules_context():
    row = _row("so good @tacosmanny", handle="tacosmanny")
    c = rk.Candidate("Tacos Manny", {"mention"})
    f = rk.features(c, row, rules_none=True)
    assert f["is_poster"] == 1.0 and f["src_mention"] == 1.0
    assert f["rules_none"] == 1.0 and f["src_mention_when_rules_none"] == 1.0
    assert set(f) == set(rk.FEATURES)


def test_logistic_regression_learns_a_separable_rule():
    data = [([1.0, 0.0], 1)] * 20 + [([0.0, 1.0], 0)] * 60
    w = rk.train(data, l2=0.001, epochs=300)
    assert rk.prob(w, [1.0, 0.0]) > 0.8 and rk.prob(w, [0.0, 1.0]) < 0.2


def test_pick_returns_no_venue_below_the_threshold():
    reel = rk.Reel({}, None, None, [rk.Candidate("A"), rk.Candidate("B")], [[1.0], [0.0]])
    w = [0.0, 2.0]                                                 # bias 0, one feature
    assert rk.pick(w, reel, threshold=0.5) == "A"
    assert rk.pick(w, reel, threshold=0.95) is None
