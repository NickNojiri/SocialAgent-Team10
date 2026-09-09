"""Extraction accuracy, measured against the labeled corpus (`data/labels.jsonl`).

Offline + deterministic: scores the *heuristic* extractor only (no Ollama), so it
runs in CI. The LLM path is measured by `python -m src.ingestion.eval`.

These tests are a ratchet. As the slot-first parser lands (docs/EXTRACTION_ACCURACY.md
slice 3+), raise the floors and delete the xfail markers as rows start passing.
"""

import pytest

from src.ingestion.config import IngestionSettings
from src.ingestion.eval import load_labels, score

_VALID_VERDICTS = {"right", "wrong", "missing", "needs_review"}

# Ratchet. Heuristic-only, no LLM. Raise these as slices land; never lower them.
#   2026-09-08 seed baseline          : venue exact 3/11 · fuzzy 3/11 · category 10/14 · city 0/8
#   + rule set 1 (@handle / from X / quoted-name / container-demote)
#                                     : venue exact 7/11 · fuzzy 7/11 · category 10/14 · city 1/8
#   + rule set 2 (category keywords + area-hashtag/container gazetteer)
#                                     : venue exact 7/11 · fuzzy 7/11 · category 14/14 · city 7/8
#   + handle word-split (in-house, handle_split.py)
#                                     : venue exact 8/11 · fuzzy 8/11 · category 14/14 · city 7/8
#   + looks_vague() catalog-inclusion gate : + promo rejected 3/3
_FLOOR_VENUE_EXACT = 8
_FLOOR_VENUE_FUZZY = 8
_FLOOR_CATEGORY = 14
_FLOOR_CITY = 7
_FLOOR_VAGUE = 3

# Regression guards: venue name is a run-together @handle in the caption and the
# in-house word-split (handle_split.py) must keep recovering it.
_SLOT_PARSER_TARGETS = {
    "DZ_LoJovDf1": "Waterfall Chicken",
}


@pytest.fixture(scope="module")
def rows():
    return load_labels()


def test_corpus_is_wellformed(rows):
    assert rows, "label corpus is empty"
    for r in rows:
        assert r.get("url"), f"row missing url: {r}"
        assert "input" in r and "gold" in r and "verdict" in r, f"row missing sections: {r['url']}"
        for field_ in ("venue", "category"):
            v = r["verdict"].get(field_)
            assert v in _VALID_VERDICTS, f"{r['url']}: bad verdict.{field_} = {v!r}"


def test_heuristic_baseline_does_not_regress(rows):
    t = score(rows, use_llm=False, settings=IngestionSettings())
    assert t.venue_exact >= _FLOOR_VENUE_EXACT, (
        f"venue exact regressed: {t.venue_exact}/{t.venue_scored} < floor {_FLOOR_VENUE_EXACT}"
    )
    assert t.venue_fuzzy >= _FLOOR_VENUE_FUZZY, (
        f"venue fuzzy regressed: {t.venue_fuzzy}/{t.venue_scored} < floor {_FLOOR_VENUE_FUZZY}"
    )
    assert t.cat_hit >= _FLOOR_CATEGORY, (
        f"category regressed: {t.cat_hit}/{t.cat_scored} < floor {_FLOOR_CATEGORY}"
    )
    assert t.city_hit >= _FLOOR_CITY, (
        f"city regressed: {t.city_hit}/{t.city_scored} < floor {_FLOOR_CITY}"
    )
    assert t.vague_hit >= _FLOOR_VAGUE, (
        f"catalog-inclusion regressed: {t.vague_hit}/{t.vague_scored} < floor {_FLOOR_VAGUE}"
    )


@pytest.mark.parametrize("code,expected_venue", sorted(_SLOT_PARSER_TARGETS.items()))
def test_slot_parser_recoverable_venues(rows, code, expected_venue):
    row = next((r for r in rows if r["url"].rstrip("/").split("/")[-1] == code), None)
    assert row is not None, f"{code} not in corpus"

    t = score([row], use_llm=False, settings=IngestionSettings())
    got = t.rows[0]["pred_venue"]
    if t.venue_exact == 0:
        pytest.xfail(f"slot-first parser not landed yet: got {got!r}, want {expected_venue!r}")
    assert t.venue_exact == 1
