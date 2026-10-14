"""experiments/location_eval.py: the answer key it judges SpotBot's "near X" results against."""
import math

from experiments.location_eval import _CITY, answer_key, keyword_embedder, located

LAGUNA_NIGUEL = _CITY["laguna niguel"]


def _row(venue, city=None, lat=None, lng=None, tag=None, dish=(), category="food_drink"):
    return {"url": f"https://www.instagram.com/reel/{venue[:6]}/",
            "input": {"lat": lat, "lng": lng, "location_text": tag},
            "gold": {"venue": venue, "city": city, "in_catalog": True, "dish": list(dish),
                     "category": category}}


def test_location_uses_the_tag_unless_it_is_only_a_county():
    assert located(_row("A", lat=33.54, lng=-117.78, tag="Laguna Beach, California"))[2] == "tag"
    county = located(_row("B", city="Costa Mesa", lat=33.7, lng=-117.8, tag="Orange County, California"))
    assert county[:2] == _CITY["costa mesa"] and county[2] == "city centre"
    assert located(_row("C", city="Nashville")) is None          # unknown: left out of the key


def test_answer_key_is_the_nearest_matching_spots_one_per_place():
    rows = [
        _row("Takumi Sushi", city="Costa Mesa", dish=["sushi"]),
        _row("Yuzu Sushi", city="Laguna Beach", dish=["sushi"]),
        _row("Yuzu Sushi", city="Laguna Beach", dish=["sushi"]),      # second reel, same place
        _row("Wild Strawberry Cafe", city="Laguna Beach", category="cafe_dessert"),
        _row("Far Sushi", city="Nashville", dish=["sushi"]),
    ]
    key = answer_key(rows, LAGUNA_NIGUEL, lambda g: "sushi" in g["dish"])
    assert [r["gold"]["venue"] for _, r, _ in key] == ["Yuzu Sushi", "Takumi Sushi"]
    assert key[0][0] < key[1][0]


def test_stand_in_embedder_is_normalised_and_keyword_sensitive():
    a, b, c = keyword_embedder(["sushi in laguna", "sushi bar", "dessert cafe"])
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-9)
    assert sum(x * y for x, y in zip(a, b)) > sum(x * y for x, y in zip(a, c))


# ── experiments/compare_commits.py: paired scoring of two extractor versions ──

def test_paired_scores_only_scoreable_rows_and_pairs_them():
    from experiments.compare_commits import paired

    rows = [
        {"url": "u1", "gold": {"venue": "Yuzu Sushi", "category": "food_drink", "in_catalog": True},
         "verdict": {"venue": "wrong", "category": "right"}},
        {"url": "u2", "gold": {"venue": None, "category": "other", "in_catalog": False},
         "verdict": {"venue": "wrong", "category": "right"}},
        {"url": "u3", "gold": {"venue": "X"}, "verdict": {"venue": "needs_review"}},    # not scored
    ]
    before = {"u1": {"venue": "Yuzu", "category": "food_drink", "is_vague": False},
              "u2": {"venue": "Promo Co", "category": "food_drink", "is_vague": False},
              "u3": {"venue": "X", "category": None, "is_vague": False}}
    after = {"u1": {"venue": "Yuzu Sushi", "category": "food_drink", "is_vague": False},
             "u2": {"venue": None, "category": "other", "is_vague": True},
             "u3": {"venue": "X", "category": None, "is_vague": False}}
    out = paired(rows, before, after)
    assert out["venue same place"] == ([False, False], [True, True])
    assert out["category"] == ([True, False], [True, True])
    assert out["promo rejected"] == ([False], [True]) and out["real place kept"] == ([True], [True])
