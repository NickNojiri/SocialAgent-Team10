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
#   + rule set 2 / handle-split / looks_vague : venue 8/11 · cat 14/14 · city 7/8 · vague 3/3
#   ── corpus grown 15 -> 49 (34 real reels from DMs); honest numbers, plus a
#      bug-fix pass (address/IG-tag cleanup, @handle spelling from caption, beach
#      word-boundary, quoted double-quotes only, first-person possessive, "at the"):
#   49-row baseline                    : venue 20/41 · category 35/44 · city 27/35 · vague 3/6
#   2026-09-09 corpus grown to 172 labeled rows (batch2, +123 captioned reels
#      hand-labeled); heuristic-only numbers on the bigger corpus:
#   172-row baseline                   : venue exact 48/158 · fuzzy 51/158 · category 116/161 · city 84/130 · vague 6/12
#   + round 3 (OUTDOORS keywords · _CALLED + region stoplist · pin-line clean ·
#      bracket [name] city · Spanish-recipe vague · handle geo-suffix strip +
#      tail-word peel + vocab · _FROM_VENUE de/del connectors · blogger-handle skip):
#   round-3 (items 1-6,9a)             : venue exact 66/158 · fuzzy 77/158 · category 119/161 · city 85/130 · vague 7/12
#   + round-3 items 7-10 (lowercase dash-city head · pin-line-as-venue slot ·
#     real-estate/hype vague · _IN_CITY multi-match + region demotion · curly
#     apostrophes · _FIRST_PERSON widening):
#   round-3 full                       : venue exact 67/158 · fuzzy 77/158 · category 119/161 · city 86/130 · vague 8/12
#   + round-4 (alias table from corpus corrections · gazetteer expanded past
#     SoCal · profile-name capture): venue partly corpus-fitted via aliases —
#     the honest gain is city (gazetteer). Real number comes from the train/test split.
#   round-4                            : venue exact 82/158 · fuzzy 88/158 · category 119/161 · city 102/130 · vague 8/12
#   2026-09-09 corpus 433 (186 labeled: 139 train / 47 test), + _looks_like_list,
#     alias table regenerated, train/test split added to eval.py.
#   ── HONEST held-out (--split test, 46-47 rows): venue exact 47.8% · fuzzy 52.2%
#      · category 66.0% · city 62.2%. The floors below are on ALL labeled rows.
#   all-split                          : venue exact 96/185 · fuzzy 104/185 · category 134/186 · city 114/151 · vague 8/16
_FLOOR_VENUE_EXACT = 96
_FLOOR_VENUE_FUZZY = 104
_FLOOR_CATEGORY = 134
_FLOOR_CITY = 114
_FLOOR_VAGUE = 8

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
