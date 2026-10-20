"""The train / dev / test split (fixtures/splits.json, docs/DATA_PLAN.md).

Each test is one promise the data plan makes: test is the same 185 reels BASELINE.md
reports, no creator spans train and dev, new reels join their creator, silver labels
never get scored, and nobody reads test without saying why.
"""

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from src.ingestion import eval as ev

REPO = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("freeze_splits", REPO / "scripts" / "freeze_splits.py")
fs = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = fs
_spec.loader.exec_module(fs)


@pytest.fixture(scope="module")
def rows():
    return ev.load_labels()


def _reel(code, handle=None, **extra):
    return {"url": f"https://www.instagram.com/reel/{code}/", "input": {"handle": handle}, **extra}


def test_test_is_the_frozen_baseline_split_byte_for_byte(rows):
    # The original rule: a reel is test when its URL hash < 30. BASELINE.md's 185 reels.
    old = {ev._code(r["url"]) for r in rows if ev._hash100(ev._code(r["url"])) < 30}
    now = {ev._code(r["url"]) for r in rows if ev.split_of(r) == "test"}
    assert now == old and len(now) == 185


def test_dev_comes_out_of_old_train_and_no_creator_spans_train_and_dev(rows):
    counts = Counter(ev.split_of(r) for r in rows)
    assert counts["dev"] >= 100 and counts["train"] + counts["dev"] + counts["test"] == len(rows)
    sides = {}
    for r in rows:
        sides.setdefault(ev.creator_of(r), set()).add(ev.split_of(r))
    assert not [c for c, s in sides.items() if {"train", "dev"} <= s]


def test_the_manifest_still_matches_a_fresh_freeze():
    assert fs.main(["--check"]) == 0


def test_freezing_again_is_refused(capsys):
    before = ev.SPLITS_PATH.read_bytes()
    assert fs.main([]) == 1
    assert "re-freezing reshuffles" in capsys.readouterr().err
    assert ev.SPLITS_PATH.read_bytes() == before


def test_a_new_reel_joins_its_creators_split(rows):
    by_split = {}
    for r in rows:
        handle = (r.get("input") or {}).get("handle")
        if handle:
            by_split.setdefault(ev.split_of(r), handle)
    for split, handle in by_split.items():
        known = ev._manifest()["creators"][ev.creator_of({"url": "x", "input": {"handle": handle}})]
        assert ev.split_of(_reel("BRANDNEWCODE1", handle)) == known


def test_unknown_creators_split_about_60_20_20_and_deterministically():
    reels = [_reel(f"NEW{i}", f"newcreator{i}") for i in range(3000)]
    counts = Counter(ev.split_of(r) for r in reels)
    assert 0.55 < counts["train"] / 3000 < 0.65
    assert 0.16 < counts["dev"] / 3000 < 0.24 and 0.16 < counts["test"] / 3000 < 0.24
    assert [ev.split_of(r) for r in reels[:50]] == [ev.split_of(r) for r in reels[:50]]
    # one creator, many reels: all in one split
    assert len({ev.split_of(_reel(f"SAME{i}", "onecreator")) for i in range(40)}) == 1


def test_silver_labels_are_always_train(rows):
    test_row = next(r for r in rows if ev.split_of(r) == "test")
    assert ev.split_of({**test_row, "source": "silver"}) == "train"
    assert ev.split_of(_reel("SILVER1", "newcreator7", source="silver")) == "train"


def test_reading_test_needs_a_reason_and_is_logged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "TEST_LEDGER", tmp_path / "TEST_LEDGER.md")
    with pytest.raises(SystemExit) as err:
        ev.read_test(None, "eval")
    assert "--final" in str(err.value)
    assert not (tmp_path / "TEST_LEDGER.md").exists()

    ev.read_test("Sprint 10 | final number", "eval")
    ev.read_test("second look", "compare_commits")
    ledger = (tmp_path / "TEST_LEDGER.md").read_text(encoding="utf-8")
    assert ev.ledger_reads() == 2
    assert "| eval | Sprint 10 / final number |" in ledger      # a "|" can't break the table
    assert "read #2" in capsys.readouterr().out


def test_the_cli_refuses_test_without_final(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["eval", "--offline", "--split", "test"])
    with pytest.raises(SystemExit) as err:
        ev.main()
    assert "refused" in str(err.value)


def test_the_dev_alias_table_holds_no_dev_reel(rows):
    from scripts.build_aliases import build, norm

    fit = build(rows, "train")
    for r in rows:
        if ev.split_of(r) != "train":
            pred = norm((r.get("predicted") or {}).get("venue"))
            gold = (r.get("gold") or {}).get("venue")
            # a dev or test reel's own correction must not be in the table that scores dev
            if pred in fit and pred != norm(gold):
                assert fit[pred] != gold or any(
                    ev.split_of(t) == "train" and norm((t.get("predicted") or {}).get("venue")) == pred
                    for t in rows)
    shipped = json.loads((REPO / "fixtures" / "venue_aliases.fit.json").read_text(encoding="utf-8"))
    assert shipped == fit


def test_unseen_creators_means_unseen_by_what_the_split_learned_from(rows):
    dev = [r for r in rows if ev.split_of(r) == "dev"]
    assert not ev.seen_creators(rows, "dev") & {ev.creator_of(r) for r in dev}   # dev is clean by construction
    test = [r for r in rows if ev.split_of(r) == "test"]
    leaked = [r for r in test if ev.creator_of(r) in ev.seen_creators(rows, "test")]
    assert len(leaked) == 50          # docs/DATA_PLAN.md caveat 2; may only shrink as labels are fixed
