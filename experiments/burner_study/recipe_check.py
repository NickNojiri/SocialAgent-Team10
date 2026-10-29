"""How does the saved M2 v3 handle recipe posts? (Recipes should be 'no venue'.)
Recipe = the parser's own recipe detectors (normalizer._RECIPE_RE / _RECIPE_CTA) on the caption,
or the gold label says not-a-place with recipe words. Counts on train (silver+gold) and dev."""
import re
import sys
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev, gold_new, nz, silver_new  # noqa: E402

RECIPE_WORDS = re.compile(r"\b(recipe|ingredients?|tbsp|tsp|tablespoons?|teaspoons?|preheat|bake at|\d+\s?(?:g|grams|cups?|oz)\b)", re.I)


def is_recipe(row):
    cap = (row.get("input") or {}).get("caption") or ""
    return bool(nz._RECIPE_RE.search(cap) or nz._RECIPE_CTA.search(cap) or len(RECIPE_WORDS.findall(cap)) >= 2)


M.OPTS.update(clean=True, parser_pick=True, accounts=True)
path = HERE.parents[1] / "data" / "models" / "reranker_v3_clean_pick_acct_frac1.0"
tok = AutoTokenizer.from_pretrained(path)
model = AutoModelForSequenceClassification.from_pretrained(path).eval()
for name, rows in (("train (gold+silver)", [r for r in corpus + gold_new + silver_new if ev.split_of(r) == "train" and M.V.scoreable(r)]),
                   ("dev", [r for r in corpus if ev.split_of(r) == "dev" and M.V.scoreable(r)])):
    recipes = [r for r in rows if is_recipe(r)]
    exs = M.build(recipes)
    preds, hits = M.evaluate(model, tok, exs, "cpu")
    gold_none = sum(1 for e in exs if not e.gold)
    named = sum(1 for p, e in zip(preds, exs) if p and not e.gold)
    print(f"{name}: {len(rows)} reels, {len(recipes)} look like recipes "
          f"({gold_none} labeled not-a-place) · M2 v3 right on {sum(hits)}/{len(exs)} · "
          f"named a venue on a not-a-place recipe: {named}")
    for p, e in zip(preds, exs):
        if not ev.venue_same_place(p, e.gold):
            print(f"    miss {ev._code(e.row['url'])}: M2 {p!r} | gold {e.gold!r}")
