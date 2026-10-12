"""The labeling toolkit (LABELING_GUIDE.md) and the extraction rules it measures.

Offline and synthetic: no row of fixtures/labels.jsonl is read or written here.
"""

import csv

import pytest

from scripts.kappa_batch import export, merge, pick
from scripts.label_audit import audit
from src.ingestion.eval import venue_same_place
from src.ingestion.pipeline.handle_split import split_handle
from src.ingestion.pipeline.normalizer import (
    _categorize, _pinned_venue, _sentence_venue, _trim_venue,
)
from src.ingestion.schemas.inspiration import EventCategory


# ── scorer: "same place" (guide §3) ─────────────────────────────────────────────

@pytest.mark.parametrize("pred,gold", [
    ("Bjs Restaurants", "BJ's Restaurants"),       # punctuation
    ("Shugarshack", "Shugar Shack"),               # spacing
    ("Phin Coffee", "Phin Coffee Torrance"),       # location tail
    ("The Shore", "The Shore OC"),
    ("Aunt Cass Cafe", "Aunt Cass Cafe, DCA"),
    ("Tahoe Bagel", "Tahoe Bagel Company"),        # business-type tail
    ("Jolly Holiday", "Jolly Holiday Bakery Cafe"),
    (None, None),                                  # promo: nothing expected, nothing given
])
def test_same_place_accepts_the_guides_equivalences(pred, gold):
    assert venue_same_place(pred, gold)


@pytest.mark.parametrize("pred,gold", [
    ("Coffee", "Coffee Company"),       # the shared part must be a real name
    ("Sunset Bar", "Sunset Cafe"),      # different places, not a tail
    ("Kissa", "Kissa by Motto"),        # 'by X' is part of some names
    ("Island Lake Diner", "Island Link Diner"),
    ("Blue Ribbon Corn Dogs", "Disneyland"),
    ("Some Venue", None),               # a promo must stay empty
    (None, "Some Venue"),
])
def test_same_place_rejects_different_places(pred, gold):
    assert not venue_same_place(pred, gold)


# ── category: kind of place first, then dishes (normalizer) ─────────────────────

@pytest.mark.parametrize("text,hashtags,expected", [
    ("Chicken pasta with extra sauce + cookie dough cheese cake", [], EventCategory.FOOD_DRINK),
    ("Mochi-like crust 📍Sei Pizza Bar 8781 W Pico Blvd", [], EventCategory.FOOD_DRINK),
    ("Brunch at this cafe: latte, breakfast burrito, salad", [], EventCategory.CAFE_DESSERT),
    ("Happy hour burgers all week", [], EventCategory.NIGHTLIFE),
    ("We tried all the best bakeries in Pasadena", [], EventCategory.CAFE_DESSERT),
    ("🎃PUMPKIN SPICE S’MORES! 👻", ["smores"], EventCategory.CAFE_DESSERT),
    ("Our highlight reel", ["bestsushiinguwahati"], EventCategory.FOOD_DRINK),
    ("the virus i got from making this vid", ["housemusic", "newmusic"], EventCategory.OTHER),
    ("goon time", [], EventCategory.OTHER),
])
def test_categorize(text, hashtags, expected):
    assert _categorize(text, hashtags) is expected


# ── venue: pins, name lines, prose, tails (normalizer) ──────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("Miopane in Pasadena", "Miopane"),
    ("Concerto • Koreatown", "Concerto"),
    ("En Familia - Mexican Steakhouse", "En Familia"),
    ("Protein Bao inside", "Protein Bao"),
    ("Blue Ribbon Corn Dogs (Downtown Disney)", "Blue Ribbon Corn Dogs"),
    ("Kobashi Ramen and", "Kobashi Ramen"),
    ("Jack in the Box", "Jack in the Box"),          # 'in the' is part of the name
])
def test_trim_venue(name, expected):
    assert _trim_venue(name) == expected


@pytest.mark.parametrize("caption,expected", [
    ("so good\n\n📍 HANA Gelateria\n\nsave this", ("HANA Gelateria", "pin")),
    ("📍 Location: Mizuri Coffee\n921 S Baldwin Ave", ("Mizuri Coffee", "pin")),
    ("📍NEW! BoBaPoP Tea Bar - San Marcos", ("BoBaPoP Tea Bar", "pin")),
    ("Melt Coffee\n📍17181 Redmond Wy 100, Redmond, WA", ("Melt Coffee", "name_line")),
    ("Yuzu Sushi Bar\n📍Costa Mesa\n103 E 17th St", ("Yuzu Sushi Bar", "name_line")),
    ("📍 @theguilddubai DIFC", (None, "none")),               # a handle + district, no name
    ("📍 서울 송파구 백제고분로45길 19 2층", (None, "none")),     # an address in any script
    ("Family owned & operated\n📍 4 Main St", (None, "none")),  # a slogan, not a name line
])
def test_pinned_venue(caption, expected):
    assert _pinned_venue(caption, []) == expected


@pytest.mark.parametrize("caption,expected", [
    ("Why order one pasta?\n\nMessina is serving up pasta flights", "Messina"),
    ("One visit to Kobashi Ramen and we tried everything", "Kobashi Ramen"),
    ("Bao Chick in Irvine was voted one of the best", "Bao Chick"),
    ("This is the best spot. It was so good", None),
    ("Mango is the best fruit", None),        # a food word is not a venue
])
def test_sentence_venue(caption, expected):
    assert _sentence_venue(caption) == expected


@pytest.mark.parametrize("handle,expected", [
    ("tuttobellegelato", "Tuttobelle Gelato"),   # one name part + a known word
    ("cheffei", "Chef Fei"),
    ("alwaysbutterdays", "Always Butter Days"),
    ("waterfallchicken", "Waterfall Chicken"),   # unchanged behaviour
    ("erewhon", "Erewhon"),                      # no known words: left whole
    ("eggbred", "Eggbred"),                      # 'eggb|red' is a cut, not a split
])
def test_split_handle_allows_one_name_part(handle, expected):
    assert split_handle(handle) == expected


# ── label audit (guide §2–§6) ───────────────────────────────────────────────────

def _row(code, venue, caption="", in_catalog=True, labeler="unknown", city=None):
    return {"url": f"https://www.instagram.com/reel/{code}/", "labeler": labeler,
            "input": {"caption": caption},
            "gold": {"venue": venue, "city": city, "category": "food_drink", "in_catalog": in_catalog}}


def test_audit_flags_each_rule():
    found = audit([
        _row("A1", "Yamas Teriyaki House", "yamas teriyaki house"),
        _row("A2", "yamas teriyaki", "yamas teriyaki"),
        _row("A3", "Disneyland", "disneyland eats"),
        _row("A4", "Hidden Spot", "nothing about it"),
        _row("A5", "Promo Venue", "promo venue", in_catalog=False, labeler="nick"),
    ])
    assert len(found["spelling"]) == 1 and "A1" in found["spelling"][0]
    assert any("A3" in f for f in found["place"])
    assert any("A4" in f for f in found["missing"])
    assert any("A5" in f for f in found["conflict"])
    assert len(found["provenance"]) == 4


# ── kappa batch: export a blind sheet, import a second label ────────────────────

def test_kappa_batch_round_trip(tmp_path):
    rows = [_row(f"K{i}", f"Venue {i}", labeler="nick") for i in range(10)]
    assert [r["url"] for r in pick(rows, 4, seed=1)] == [r["url"] for r in pick(rows, 4, seed=1)]

    sheet = tmp_path / "kappa.csv"
    assert export(rows, sheet, n=4, seed=1) == 4
    with sheet.open(encoding="utf-8", newline="") as f:
        lines = list(csv.DictReader(f))
    assert "Venue" not in sheet.read_text(encoding="utf-8")       # blind: no gold leaks
    for line in lines:
        line.update(venue="Answer", city="LA", category="food_drink", in_catalog="yes")
    with sheet.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lines[0]))
        w.writeheader()
        w.writerows(lines)

    assert merge(rows, sheet, labeler="valeria", primary=None) == 4
    second = [r["second_label"] for r in rows if "second_label" in r]
    assert len(second) == 4 and all(s["independent"] and s["labeler"] == "valeria" for s in second)
    assert second[0]["gold"] == {"venue": "Answer", "city": "LA", "category": "food_drink", "in_catalog": True}
    assert all(r["gold"]["venue"].startswith("Venue") for r in rows)    # gold never touched


def test_kappa_batch_refuses_self_agreement(tmp_path):
    rows = [_row("S1", "Venue", labeler="nick")]
    sheet = tmp_path / "k.csv"
    export(rows, sheet, n=1, seed=0)
    text = sheet.read_text(encoding="utf-8").replace(",,,,,", ",X,LA,food_drink,yes,")
    sheet.write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit):
        merge(rows, sheet, labeler="nick", primary=None)


# ── label confidence: measured per-slot confidence, least sure first ────────────

def test_label_confidence_calibrates_on_train_and_ranks():
    from scripts.label_confidence import rank
    from src.ingestion.eval import split_of
    train = next(c for c in (f"T{i}" for i in range(500))
                 if split_of(f"https://www.instagram.com/reel/{c}/") == "train")
    rows = [_row(train, "HANA Gelateria", "so good\n\n📍 HANA Gelateria\n\nsave this"),
            _row("Q2", None, "goon time")]
    queue = rank(rows, {"pin": 0.9, "none": 0.3})
    assert [e["slot"] for e in queue] == ["none", "pin"]          # least confident first
    assert queue[1]["venue"] == "HANA Gelateria" and queue[1]["confidence"] == 0.9
    assert rank([{"url": "u", "input": {}}], {}) == []            # fetch failed: skipped


# ── post date from the shortcode (deal/event expiry needs the year) ─────────────

def test_posted_at_decodes_the_shortcode():
    from scripts.ig_dates import posted_at
    assert posted_at("https://www.instagram.com/reel/Dc161MuvZYC/").date().isoformat() == "2026-09-03"
    assert posted_at("DcOl-mBs5A1").date().isoformat() == "2026-08-19"
    assert posted_at("https://www.instagram.com/reel/DdeX2SesDSF/?igsh=x") is not None
    assert posted_at("not a code!") is None


# ── error-bucket fixes (experiments/error_buckets.py, train split) ─────────────

def _snap(caption, handle="", author_name=None, hashtags=()):
    from src.ingestion.eval import raw_from_input
    return raw_from_input("https://www.instagram.com/reel/X1/",
                          {"caption": caption, "handle": handle, "author_name": author_name,
                           "hashtags": list(hashtags)})


def test_business_posting_about_itself_is_the_venue():
    from src.ingestion.pipeline.normalizer import normalize
    c = normalize(_snap("Our highlight reel 🫶", handle="thesmithrestaurant"))
    assert venue_same_place(c["venue_name"], "The Smith") and c["venue_slot"] == "own_post"
    c = normalize(_snap("Give us all the pasta. Open daily 11-9 📍 4 Pine Ave", handle="ellies_lbc"))
    assert c["venue_slot"] == "own_post"


def test_reviewers_and_media_saying_our_are_not_the_venue():
    from src.ingestion.pipeline.normalizer import normalize
    for handle in ("socalnation", "visittemecula", "uyi_1993", "dylaneatss"):
        c = normalize(_snap("our favorite spots this weekend, come try them", handle=handle))
        assert c["venue_slot"] != "own_post", handle


def test_credit_handles_are_not_venues():
    from src.ingestion.pipeline.normalizer import _venue_slot
    raw = _snap("My top 5 matcha spots in LA 🍵 who am I missing? 🎥 @baegopadad")
    assert _venue_slot(raw, raw.caption)[0] != "Baegopadad"


def test_a_city_location_tag_is_not_the_venue():
    from src.ingestion.eval import raw_from_input
    from src.ingestion.pipeline.normalizer import normalize
    raw = raw_from_input("https://www.instagram.com/reel/X2/",
                         {"caption": "so good #foodie", "location_text": "Miami, Florida"})
    assert normalize(raw)["venue_name"] != "Miami"


def test_dish_bullets_and_branch_pins_are_not_a_list():
    from src.ingestion.pipeline.normalizer import _looks_like_list
    dishes = "NEW coffee spot @fikafikacoffee\n- Matcha latte\n- Ube cake\n- Hojicha\n- Croissant\n📍 Los Angeles"
    branches = "French toast!\n📍Miami Beach - 1201 Washington Ave.\n📍Wynwood - 2750 NW 3rd Ave.\n📍Biscayne - 1040 Biscayne Blvd.\n📍Coral Gables - 2300 Ponce"
    ranking = "My top 3:\n1. Stagger\n2. Kettl\n3. Damo"
    assert not _looks_like_list(_snap(dishes), dishes)
    assert not _looks_like_list(_snap(branches), branches)
    assert _looks_like_list(_snap(ranking), ranking)


def test_one_clear_recipe_cue_keeps_it_out_of_the_catalog():
    from src.ingestion.pipeline.normalizer import normalize
    c = normalize(_snap('Cheeseburger + crispy taquitos. Comment RECIPE below! @califiafarms'))
    assert c["is_vague"] and c["venue_name"] is None


def test_accents_do_not_make_a_different_place():
    assert venue_same_place("Wild Strawberry Café", "Wild Strawberry Cafe")
