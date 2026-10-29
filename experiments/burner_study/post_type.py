"""Test 7: post-type classifier, a second multiclass task.

What kind of post is this reel? 9 classes:
  place, list, later (promoter event, spot announced later),
  recipe, ad, meme, game, music, other   (the "not a place" reasons from the review page)

Labels: Nick's review verdicts (gold) for the burner reels; Claude's first-pass labels for the
undecided burner reels (silver, train only). Same frozen creator splits: train on train, report
on dev; test is not read. "Can't tell" reels are left out.

Models (same inputs as M2, minus the parser's pick):
  B0  majority class
  B1  TF-IDF (words 1-2 + characters 3-5) + logistic regression, class-balanced
  M   the multilingual MiniLM encoder fine-tuned as a 9-way classifier (cross-entropy, AdamW)
Reports accuracy, macro-F1 and per-class recall on dev. Writes compare/post_type.md.
"""
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.pipeline import make_pipeline, make_union
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import clean_inputs as CI  # noqa: E402
import compare_models as C  # noqa: E402
import ml_reranker as M  # noqa: E402
from burner_common import ev, gold_new, silver_new  # noqa: E402

CLASSES = ["place", "list", "later", "recipe", "ad", "meme", "game", "music", "other"]
BASE = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


def load_labels():
    decs = {}
    for p in (M.DATA / "experiments" / "review_decisions").glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        decs[p.stem] = d.get("data", d)
    first = {r["code"]: r for r in json.loads((M.DATA / "dm_firstpass.json").read_text(encoding="utf-8"))}

    def cls(kind_or_verdict, why):
        if kind_or_verdict in ("ok", "fixed", "not_found", "place", "unnamed"):
            return "place"
        if kind_or_verdict in ("list", "later"):
            return kind_or_verdict
        if kind_or_verdict == "none":
            return why if why in CLASSES else "other"
        return None                                     # "unknown" (can't tell)

    out = []
    for row in gold_new + silver_new:
        c = ev._code(row["url"])
        if c in decs:
            y = cls(decs[c].get("verdict"), decs[c].get("why"))
        elif c in first:
            y = cls(first[c].get("kind"), first[c].get("why"))
        else:
            y = None
        if y:
            out.append((row, y, c in decs))
    return out


def text_of(row):
    return M.context(CI.enrich_row(row))


def main():
    M.OPTS.update(clean=True, parser_pick=False, accounts=True, recipe_cue=False, llm_hint=False)
    data = load_labels()
    train = [(text_of(r), y, g) for r, y, g in data if ev.split_of(r) == "train"]
    dev = [(text_of(r), y) for r, y, g in data if ev.split_of(r) == "dev" and g]           # gold only
    print(f"train {len(train)} ({sum(g for _, _, g in train)} gold) | dev {len(dev)} gold", flush=True)
    xtr, ytr = [t for t, _, _ in train], [y for _, y, _ in train]
    wtr = [1.0 if g else 0.5 for _, _, g in train]
    xdv, ydv = [t for t, _ in dev], [y for _, y in dev]
    results = {}

    major = Counter(ytr).most_common(1)[0][0]
    results["B0 majority"] = [major] * len(xdv)

    tfidf = make_pipeline(make_union(TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
                                     TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
                          LogisticRegression(max_iter=3000, class_weight="balanced", C=4.0))
    tfidf.fit(xtr, ytr, logisticregression__sample_weight=wtr)
    results["B1 TF-IDF + logistic regression"] = list(tfidf.predict(xdv))

    random.seed(13)
    torch.manual_seed(13)
    tok = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForSequenceClassification.from_pretrained(BASE, num_labels=len(CLASSES), ignore_mismatched_sizes=True)
    counts = Counter(ytr)
    cw = torch.tensor([len(ytr) / (len(CLASSES) * max(1, counts[c])) for c in CLASSES], dtype=torch.float)
    opt = torch.optim.AdamW(model.parameters(), lr=3e-5, weight_decay=0.01)
    order = list(range(len(xtr)))
    t0 = time.time()
    for epoch in range(4):
        model.train()
        random.shuffle(order)
        for k in range(0, len(order), 16):
            idx = order[k:k + 16]
            enc = tok([xtr[i] for i in idx], truncation=True, max_length=256, padding=True, return_tensors="pt")
            logits = model(**enc).logits
            y = torch.tensor([CLASSES.index(ytr[i]) for i in idx])
            w = torch.tensor([wtr[i] for i in idx])
            loss = (torch.nn.functional.cross_entropy(logits, y, weight=cw, reduction="none") * w).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            opt.zero_grad()
        print(f"  epoch {epoch + 1} ({time.time() - t0:.0f}s)", flush=True)
    model.eval()
    preds = []
    with torch.no_grad():
        for k in range(0, len(xdv), 32):
            enc = tok(xdv[k:k + 32], truncation=True, max_length=256, padding=True, return_tensors="pt")
            preds += [CLASSES[j] for j in model(**enc).logits.argmax(-1).tolist()]
    results["M fine-tuned MiniLM classifier"] = preds

    present = [c for c in CLASSES if c in ydv]
    lines = ["# Post-type classifier (9 classes)", "",
             f"Train {len(train)} reels ({sum(g for _, _, g in train)} gold, rest silver at half weight) · "
             f"dev {len(dev)} gold reels: " + ", ".join(f"{c} {ydv.count(c)}" for c in present), "",
             "| model | accuracy | macro-F1 (classes in dev) | " + " | ".join(f"{c} recall" for c in present) + " |",
             "|---|---|---|" + "---|" * len(present)]
    for name, p in results.items():
        acc = np.mean([a == b for a, b in zip(p, ydv)])
        f1 = f1_score(ydv, p, labels=present, average="macro", zero_division=0)
        rec = [f"{sum(1 for a, b in zip(p, ydv) if b == c and a == c)}/{ydv.count(c)}" for c in present]
        lines.append(f"| {name} | {acc:.1%} | {f1:.2f} | " + " | ".join(rec) + " |")
    lines += ["", "Small classes (a few dev reels each) make per-class numbers noisy; read them as a first look."]
    text = "\n".join(lines)
    (C.OUT / "post_type.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
