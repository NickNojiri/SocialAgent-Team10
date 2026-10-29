"""Selective prediction for M2 v3: show a venue card only when the model is confident,
otherwise fall back to a summary card (what the reel is about + the creator + the link).

For each confidence threshold: coverage (share of reels that get a venue card) and precision
(share of those venue cards that name the right place). Reels the model calls "no venue" are
counted separately. The threshold is CHOSEN on the held-out train creators (validation) and
only REPORTED on dev, so dev isn't used to tune it."""
import random
import sys
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev, gold_new, silver_new  # noqa: E402

M.OPTS.update(clean=True, parser_pick=True, accounts=True)
path = HERE.parents[1] / "data" / "models" / "reranker_v3_clean_pick_acct_frac1.0"
tok, model = AutoTokenizer.from_pretrained(path), AutoModelForSequenceClassification.from_pretrained(path).eval()


def scored(rows):
    out = []
    for ex in M.build(rows):
        s = M.scores_for(model, tok, ex, "cpu").float()
        p = torch.softmax(s, 0).numpy()
        j = int(np.argmax(p))
        pick = None if ex.options[j] == M.NONE else ex.options[j]
        out.append((pick, float(p[j]), ex.gold, ex.rules_pred))
    return out


train_rows = [r for r in corpus + gold_new + silver_new if ev.split_of(r) == "train" and M.V.scoreable(r)]
creators = sorted({ev.creator_of(r) for r in train_rows})
random.Random(13).shuffle(creators)                       # same validation creators as training used
val_c = set(creators[:max(1, int(0.15 * len(creators)))])
val = scored([r for r in train_rows if ev.creator_of(r) in val_c and r.get("source") != "silver"])
dev = scored([r for r in corpus if ev.split_of(r) == "dev" and M.V.scoreable(r)])


def table(items, name):
    print(f"\n{name} ({len(items)} reels)")
    print("  threshold | venue cards (coverage) | right among them (precision) | summary cards instead")
    for t in (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95):
        cards = [(p, g) for p, c, g, _ in items if p and c >= t]
        right = sum(bool(ev.venue_same_place(p, g)) for p, g in cards)
        print(f"     {t:4.2f}   | {len(cards):3} ({100 * len(cards) / len(items):5.1f}%)          | "
              f"{right:3}/{len(cards):<3} ({100 * right / max(1, len(cards)):5.1f}%)             | {len(items) - len(cards)}")
    # agreement gate: a venue card only when M2 and the rule parser name the same place
    agree = [(p, g) for p, c, g, rp in items if p and rp and ev.venue_same_place(p, rp)]
    right = sum(bool(ev.venue_same_place(p, g)) for p, g in agree)
    tol = sum(M.tolerant(p, g) for p, g in agree)
    print(f"  AGREEMENT GATE (M2 = parser): {len(agree)} venue cards ({100 * len(agree) / len(items):.1f}%), "
          f"right {right}/{len(agree)} ({100 * right / max(1, len(agree)):.1f}%), "
          f"right incl. near-misses {tol}/{len(agree)} ({100 * tol / max(1, len(agree)):.1f}%)")


table(val, "VALIDATION (held-out train creators): choose the threshold here")
table(dev, "DEV: report only")
