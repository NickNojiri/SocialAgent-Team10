"""Candidate ranker: list every name a reel could be about, score each, pick the best.

    python experiments/ranker.py              # train on train, compare with the rules on dev
    python experiments/ranker.py --weights    # + what the model learned, feature by feature
    python experiments/ranker.py --show 5     # + 5 dev reels where ranker and rules disagree

Why: on dev, more than half the venue misses are "the right name was in the caption and
we didn't pick it" (experiments/error_buckets.py). The rules try slots in a fixed order
and stop at the first hit; this looks at every candidate together and weighs the evidence.

How:
  1. candidates()  every name the reel offers: the rules' own pick, @mentions, the 📍 line,
                   "at X" / "from X" / quoted / called, the location tag, the account's
                   name, hashtags, Title Case phrases. Same name from two places = one
                   candidate with both sources.
  2. features()    simple yes/no facts about each candidate (where it came from, is it the
                   poster, a city, a business word, how often it appears...).
  3. train()       logistic regression, plain Python, L2-regularized. A candidate is
                   positive when it is the same place as the gold venue.
  4. pick()        the top-scoring candidate, or no venue when even the best score is below
                   a threshold (promos, recipes). The threshold and the L2 strength are
                   chosen by 5-fold cross-validation on train, grouped by creator.

Rules of the road (docs/DATA_PLAN.md): it learns from train only, is compared on dev,
and never reads test unless --split test --final "why".
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import (  # noqa: E402
    _SCOREABLE_VERDICTS, creator_of, format_rate, load_labels, mcnemar_exact,
    paired_delta_interval, raw_from_input, read_test, split_of, use_aliases, venue_same_place,
)
from src.ingestion.pipeline import normalizer as nz  # noqa: E402

VENUE_LIST = REPO / "fixtures" / "venues_socal.json"   # OpenStreetMap names; scripts/build_venue_list.py

_TITLE_PHRASE = re.compile(r"(?<![@#\w])([A-Z][\w'’&.]*(?:\s+(?:&|and|of|de|la|el|the|[A-Z0-9][\w'’&.]*)){1,4})")
_HASHTAG = re.compile(r"#(\w{4,30})")
_GENERIC_TAGS = re.compile(r"^(?:food|foodie|eats|la|socal|oc|yum|reels?|explore|fyp|viral|"
                           r"\w*(?:food|eats|foodie|blogger|life|vibes|gram|things|guide))$", re.I)


def sq(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# ── 1. candidates ──────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    name: str
    sources: set[str] = field(default_factory=set)
    rules_slot: str = ""                  # set when this is the rules' own pick


def candidates(row: dict, rules_pick: tuple[str | None, str]) -> list[Candidate]:
    inp = row.get("input") or {}
    caption = inp.get("caption") or ""
    author = (inp.get("handle") or "").lstrip("@")
    found: dict[str, Candidate] = {}

    def add(name: str | None, source: str, slot: str = "") -> None:
        name = (name or "").strip(" .,!?:;\"'()[]·-–—")
        key = sq(name)
        if len(key) < 3 or len(name) > 60:
            return
        cand = found.setdefault(key, Candidate(name))
        cand.sources.add(source)
        if slot:
            cand.rules_slot = slot

    pick, slot = rules_pick
    if pick:
        add(pick, "rules", slot)
    for h in dict.fromkeys(nz._ANY_HANDLE.findall(caption)):
        if h.lower() != author.lower():
            add(nz._caption_spelling(nz._handle_to_name(h), caption), "mention")
    for m in nz._PIN_LINE.finditer(caption):
        add(nz._trim_venue((nz._clean_pin(m.group("loc")) or "").split(",")[0]), "pin")
    for rx, source in ((nz._AT_VENUE, "at"), (nz._FROM_VENUE, "from"), (nz._CALLED, "called")):
        for m in rx.finditer(caption):
            add(nz._trim_venue(m.group("venue")), source)
    for m in nz._QUOTED_NAME.finditer(caption):
        add(m.group(1), "quoted")
    dash, _ = nz._name_dash_city(caption)
    add(dash, "dash")
    loc = (inp.get("location_text") or "").split(",")[0]
    add(loc, "location_tag")
    if author:
        add(nz._clean_display_name(inp.get("author_name")) or nz._handle_to_name(author), "author")
    add(inp.get("og_title"), "title")
    for tag in _HASHTAG.findall(caption):
        if not _GENERIC_TAGS.match(tag):
            add(nz._handle_to_name(tag), "hashtag")
    for m in _TITLE_PHRASE.finditer(caption[:600]):
        add(m.group(1), "titlecase")
    return list(found.values())


# ── 2. features ────────────────────────────────────────────────────────────────

SOURCES = ("rules", "mention", "pin", "at", "from", "called", "quoted", "dash",
           "location_tag", "author", "title", "hashtag", "titlecase")
STRONG_SLOTS = ("pin", "handle_from", "jsonld", "quoted", "from_titlecase", "name_line", "alias")


def _venue_names() -> set[str]:
    try:
        data = json.loads(VENUE_LIST.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return set()
    return {sq(n) for n in (data.get("names", []) if isinstance(data, dict) else data) if len(sq(n)) >= 4}


_VENUES = _venue_names()


def features(c: Candidate, row: dict, rules_none: bool = False) -> dict[str, float]:
    inp = row.get("input") or {}
    caption = inp.get("caption") or ""
    key, flat_caption = sq(c.name), sq(caption)
    author = sq(inp.get("handle"))
    words = c.name.split()
    first = caption.lower().find(c.name.lower())
    f = {f"src_{s}": float(s in c.sources) for s in SOURCES}
    f.update({
        "n_sources": min(len(c.sources), 4) / 4,
        "rules_strong_slot": float(c.rules_slot in STRONG_SLOTS),
        "rules_weak_slot": float(bool(c.rules_slot) and c.rules_slot not in STRONG_SLOTS),
        "is_poster": float(bool(author) and (key == author or (len(key) > 4 and (key in author or author in key)))),
        "poster_is_business": float(nz._is_business_account(caption, (inp.get("handle") or "").lstrip("@").lower())),
        "poster_is_blogger": float(bool(nz._BLOGGER_HANDLE.search(inp.get("handle") or ""))),
        "blogger_name": float(bool(nz._BLOGGER_HANDLE.search(key))),
        "is_region": float(nz._is_region(c.name)),
        "is_container": float(nz._is_container(c.name)),
        "business_word": float(bool(nz._PIN_VENUE_KW.search(c.name))),
        "has_digit": float(bool(re.search(r"\d", c.name))),
        "one_word": float(len(words) == 1),
        "long_name": float(len(words) >= 5),
        "times_in_caption": min(flat_caption.count(key), 4) / 4 if key else 0.0,
        "early": float(0 <= first < 120),
        "in_first_line": float(c.name.lower() in caption.split("\n")[0].lower()),
        "on_venue_list": float(key in _VENUES),
        "post_is_list": float(nz._looks_like_list(raw_from_input(row["url"], inp), caption)),
    })
    # When the rules found nothing, every candidate starts with no "rules" vote, so the
    # model needs to know that's the situation, and which sources to trust in it.
    none = float(rules_none)
    f["rules_none"] = none
    for k in ("src_mention", "src_pin", "src_location_tag", "src_author", "src_at", "src_titlecase",
              "is_poster", "poster_is_business", "business_word", "early"):
        f[f"{k}_when_rules_none"] = f[k] * none
    return f


FEATURES = sorted(features(Candidate("Sample Cafe", {"rules"}), {"url": "u", "input": {}}))


# ── 3. logistic regression, plain Python ───────────────────────────────────────

def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def train(examples: list[tuple[list[float], int]], l2: float = 0.1, epochs: int = 300,
          lr: float = 0.5) -> list[float]:
    """Weights (bias first) minimizing log loss + l2·|w|². Positives are rare (one per
    reel at most), so each class gets equal total weight."""
    n_feat = len(examples[0][0])
    w = [0.0] * (n_feat + 1)
    pos = sum(y for _, y in examples) or 1
    neg = (len(examples) - pos) or 1
    cw = {1: len(examples) / (2 * pos), 0: len(examples) / (2 * neg)}
    for _ in range(epochs):
        grad = [0.0] * len(w)
        for x, y in examples:
            err = (_sigmoid(w[0] + sum(wi * xi for wi, xi in zip(w[1:], x))) - y) * cw[y]
            grad[0] += err
            for i, xi in enumerate(x):
                if xi:
                    grad[i + 1] += err * xi
        n = len(examples)
        w[0] -= lr * grad[0] / n
        for i in range(1, len(w)):
            w[i] -= lr * (grad[i] / n + 2 * l2 * w[i])
    return w


def prob(w: list[float], x: list[float]) -> float:
    return _sigmoid(w[0] + sum(wi * xi for wi, xi in zip(w[1:], x)))


# ── 4. the reel-level pipeline ─────────────────────────────────────────────────

@dataclass
class Reel:
    row: dict
    gold: str | None
    rules_pred: str | None
    cands: list[Candidate]
    xs: list[list[float]]


def scoreable(row: dict) -> bool:
    gold, verdict = row.get("gold") or {}, row.get("verdict") or {}
    return verdict.get("venue") in _SCOREABLE_VERDICTS and bool(gold.get("venue") or gold.get("in_catalog") is False)


def prepare(rows: list[dict]) -> list[Reel]:
    reels = []
    for r in rows:
        if not scoreable(r):
            continue
        out = nz.normalize(raw_from_input(r["url"], r.get("input") or {}))
        cands = candidates(r, (out.get("venue_name"), out.get("venue_slot") or ""))
        none = not out.get("venue_name")
        xs = [[features(c, r, none)[k] for k in FEATURES] for c in cands]
        reels.append(Reel(r, (r.get("gold") or {}).get("venue"), out.get("venue_name"), cands, xs))
    return reels


def examples(reels: list[Reel]) -> list[tuple[list[float], int]]:
    return [(x, int(venue_same_place(c.name, r.gold))) for r in reels for c, x in zip(r.cands, r.xs)]


def pick(w: list[float], reel: Reel, threshold: float) -> str | None:
    if not reel.cands:
        return None
    best, x = max(zip(reel.cands, reel.xs), key=lambda cx: prob(w, cx[1]))
    return best.name if prob(w, x) >= threshold else None


def hits(preds: list[str | None], reels: list[Reel]) -> list[bool]:
    return [venue_same_place(p, r.gold) for p, r in zip(preds, reels)]


THRESHOLDS = [i / 20 for i in range(1, 19)]
L2S = (0.001, 0.01, 0.1)


def choose(reels: list[Reel], folds: int = 5, seed: int = 491) -> tuple[float, float, float]:
    """(l2, threshold, CV accuracy) by k-fold cross-validation over train, grouped by
    creator so a fold never sees a creator it is scored on."""
    groups = sorted({creator_of(r.row) for r in reels})
    random.Random(seed).shuffle(groups)
    fold_of = {g: i % folds for i, g in enumerate(groups)}
    best = (L2S[0], 0.5, -1.0)
    for l2 in L2S:
        right = Counter()
        for k in range(folds):
            fit = [r for r in reels if fold_of[creator_of(r.row)] != k]
            held = [r for r in reels if fold_of[creator_of(r.row)] == k]
            w = train(examples(fit), l2=l2)
            for t in THRESHOLDS:
                right[t] += sum(hits([pick(w, r, t) for r in held], held))
        t, n = max(right.items(), key=lambda kv: (kv[1], -abs(kv[0] - 0.5)))
        if n / len(reels) > best[2]:
            best = (l2, t, n / len(reels))
    return best


def oracle(reels: list[Reel]) -> int:
    """Reels where some candidate is right (or there is no venue): the ranker's ceiling."""
    return sum(1 for r in reels if not r.gold or any(venue_same_place(c.name, r.gold) for c in r.cands))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=["dev", "test"], default="dev", help="where to compare (default dev)")
    ap.add_argument("--final", metavar="WHY", help="required with --split test (logged in docs/results/TEST_LEDGER.md)")
    ap.add_argument("--weights", action="store_true", help="print the learned weight of every feature")
    ap.add_argument("--show", type=int, default=0, help="print N reels where the ranker and the rules disagree")
    args = ap.parse_args(argv)
    if args.split == "test":
        read_test(args.final, "ranker")

    rows = load_labels()
    use_aliases("fit")                                   # the rules see train corrections only
    train_reels = prepare([r for r in rows if split_of(r) == "train"])
    if args.split == "test":
        use_aliases("train")                             # refit on train + dev, then test once
        train_reels += prepare([r for r in rows if split_of(r) == "dev"])
    held = prepare([r for r in rows if split_of(r) == args.split])

    l2, threshold, cv = choose(train_reels)
    w = train(examples(train_reels), l2=l2)
    print(f"ranker: {len(FEATURES)} features · l2={l2} · threshold={threshold} "
          f"(5-fold CV on train, by creator: {cv:.1%})"
          + ("" if _VENUES else " · no venue list yet (scripts/build_venue_list.py)") + "\n")

    for name, reels in (("train", train_reels), (args.split, held)):
        rules = hits([r.rules_pred for r in reels], reels)
        ranker = hits([pick(w, r, threshold) for r in reels], reels)
        b_only, a_only, p = mcnemar_exact(rules, ranker)
        ci = paired_delta_interval(rules, ranker)
        print(f"{name:>5}  {len(reels)} reels · candidates hold the answer for "
              f"{format_rate(oracle(reels), len(reels))}")
        print(f"       rules  : {format_rate(sum(rules), len(rules))}")
        print(f"       ranker : {format_rate(sum(ranker), len(ranker))}")
        delta = sum(ranker) - sum(rules)
        print(f"       change : {delta / len(reels):+.1%}"
              + (f" CI [{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "")
              + f" · rules only {b_only}, ranker only {a_only}, McNemar p={p:.3g}\n")

    if args.weights:
        print("learned weights (positive = makes a candidate more likely the venue):")
        for k, v in sorted(zip(FEATURES, w[1:]), key=lambda kv: -abs(kv[1])):
            print(f"  {v:+7.2f}  {k}")
        print(f"  {w[0]:+7.2f}  (bias)\n")

    if args.show:
        shown = 0
        for r in held:
            ours = pick(w, r, threshold)
            if bool(venue_same_place(ours, r.gold)) == bool(venue_same_place(r.rules_pred, r.gold)):
                continue
            print(f"  gold={r.gold!r}  rules={r.rules_pred!r}  ranker={ours!r}  {r.row['url']}")
            shown += 1
            if shown >= args.show:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
