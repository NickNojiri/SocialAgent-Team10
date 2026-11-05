"""The M2 place picker in the pipeline (pipeline/place_picker.py), with a stand-in scorer.

The real model needs torch and the trained weights (data/models/, never in git); these tests
check everything around it: candidates, the training input format, and that the rules'
answer stands whenever the picker abstains or fails.
"""
import pytest

from src.ingestion.pipeline import place_picker as pp
from src.ingestion.pipeline.validator import build_record
from src.ingestion.schemas.snapshot import RawPostSnapshot

CAP = ("Late-night birria tacos pop-up 📍 Casa Loma, 4th St, Long Beach. "
       "Thanks @tacosdeltio for the collab! #birria #LongBeachFood")


def raw(**kw):
    base = dict(source_url="https://www.instagram.com/reel/ABCDE12345/", platform="instagram", extractor="t",
                caption=CAP, author_handle="bitesoflb", author_name="Bites of LB", transcript="best birria in long beach",
                frame_text="CASA LOMA")
    base.update(kw)
    return RawPostSnapshot(**base)


def by_name(name):
    """A scorer that likes one option most."""
    return lambda options, ctx: [10.0 if o == name else 0.0 for o in options]


def test_candidates_include_the_rules_pick_first_and_the_captions_other_names():
    names = [c.name for c in pp.candidates(raw(), "Casa Loma")]
    assert names[0] == "Casa Loma"
    assert len(names) <= pp.MAX_CANDS and len(set(n.lower() for n in names)) == len(names)
    assert any("tio" in n.lower() for n in names)                  # the @mention


def test_context_matches_the_training_format():
    ctx = pp.context(raw(), "Casa Loma")
    assert ctx.startswith("poster: @bitesoflb (Bites of LB) | parser pick: Casa Loma | caption: ")
    assert "said in video: best birria in long beach" in ctx and ctx.endswith("on screen: CASA LOMA")
    recipe = pp.context(raw(caption="My recipe: 2 cups flour, 1 tbsp sugar, preheat to 350"), None)
    assert "parser pick: none" in recipe and "post looks like: recipe" in recipe


def test_picker_returns_a_better_candidate_or_abstains():
    names = [c.name for c in pp.candidates(raw(), "Long Beach")]
    other = next(n for n in names if n != "Long Beach")
    assert pp.PlacePicker(by_name(other)).pick(raw(), "Long Beach") == other
    assert pp.PlacePicker(by_name("Long Beach")).pick(raw(), "Long Beach") is None      # agrees: keep rules
    assert pp.PlacePicker(by_name(pp.NONE)).pick(raw(), "Long Beach") is None           # "no venue": keep rules


def test_build_record_uses_the_pick_and_tags_it():
    plain, _ = build_record(raw())
    names = [c.name for c in pp.candidates(raw(), plain.venue_name)]
    want = next(n for n in names if n != plain.venue_name)          # M2 disagrees with the rules
    record, reason = build_record(raw(), picker=pp.PlacePicker(by_name(want)))
    assert reason is None and record.venue_name == want
    assert record.provenance.venue_slot == "m2" and record.provenance.extractor.endswith("+m2")


def test_a_failing_picker_never_breaks_capture():
    def boom(options, ctx):
        raise RuntimeError("model blew up")
    plain, _ = build_record(raw())
    with_bad, reason = build_record(raw(), picker=pp.PlacePicker(boom))
    assert reason is None and with_bad.venue_name == plain.venue_name


def test_off_unless_a_model_is_named(monkeypatch):
    monkeypatch.delenv("PLACE_PICKER_MODEL", raising=False)
    assert pp.from_env() is None
    monkeypatch.setenv("PLACE_PICKER_MODEL", "/nowhere/model")
    pp._LOADED.clear()
    assert pp.from_env() is None                                   # missing folder or torch: rules only
