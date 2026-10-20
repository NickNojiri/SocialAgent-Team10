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


def test_business_signals_only_lift_the_posters_own_name():
    row = _row("Our new menu is here! Open daily 11am-9pm, order online. Come see us", handle="tacosmanny")
    own = rk.features(rk.Candidate("Tacos Manny", {"author"}), row)
    other = rk.features(rk.Candidate("Boyle Heights", {"titlecase"}), row)
    assert own["poster_biz_detail"] == own["poster_own_voice"] == own["poster_order_cue"] == 1.0
    assert other["poster_biz_detail"] == other["poster_own_voice"] == 0.0
    assert set(own) | set(rk.none_features(row, {})) == set(rk.FEATURES)


def test_the_no_venue_option_knows_recipes_and_empty_rules():
    row = _row("Recipe: 2 cups flour, 1 tbsp sugar, 3 eggs. Ingredients you'll need below")
    f = rk.none_features(row, {"venue_name": None, "is_vague": True})
    assert f["none"] == f["none_rules_none"] == f["none_rules_vague"] == f["none_recipe"] == 1.0


def _reel(target, n=2):
    # option j has feature j switched on; the no-venue option has feature n
    return rk.Reel({}, None, None, [rk.Candidate(f"C{j}") for j in range(n)],
                   [[(j, 1.0)] for j in range(n)], [(n, 1.0)], target)


def test_softmax_learns_which_option_wins_and_can_pick_no_venue(monkeypatch):
    monkeypatch.setattr(rk, "FEATURES", ["a", "b", "none"])
    w = rk.train([_reel(0)] * 30 + [_reel(-1)] * 5, l2=0.001, epochs=200)   # unreachable reels are skipped
    assert rk.pick(w, _reel(0)) == "C0"
    assert abs(sum(rk.probs(w, _reel(0).options())) - 1) < 1e-9
    w = rk.train([_reel(2)] * 30, l2=0.001, epochs=200)                     # always "no venue"
    assert rk.pick(w, _reel(2)) is None
