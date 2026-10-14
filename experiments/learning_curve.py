"""What do more labels buy? Accuracy on the fixed test split as the training labels grow.

    python experiments/learning_curve.py               # sizes 50..all, 10 random draws each
    python experiments/learning_curve.py --repeats 3   # quicker

The test split never changes; only how many *train* labels each learner may see. Each size
is drawn 10 times at random, so every point is a mean ± spread, not one lucky draw.

Learners (everything that learns from labels today, plus two small trained models):
  aliases   the product's learned-correction table (scripts/build_aliases.py), rebuilt from
            the sampled labels; scored as venue same-place by the real extractor
  category  multinomial Naive Bayes over the reel's words → gold.category
            (rules today: 82.2% on test; always-majority: 60.0%)
  place     the same model → gold.in_catalog, the place-or-not gate
            (rules today: real places kept 96.4%, promos rejected 33.3%)

The slope at the end is fitted over the upper half of the curve only (accuracy ≈ a + b·log2 n
for n ≥ half the pool), because curves flatten: a fit over every point is pulled up by the
steep start and over-promises. It projects the next 100 labels (~70 land in train), which
is as far as ~430 labels can honestly see; the slope only shrinks after that.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.build_aliases import build as build_aliases  # noqa: E402
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.eval import load_labels, score, split_of, use_aliases  # noqa: E402
from src.ingestion.pipeline import normalizer  # noqa: E402

_STOP = set("the a an and or of to in on at for with is it this that my our your i you we be are was "
            "so just like get got all from by as but not have has its it's".split())


def words(row: dict) -> list[str]:
    """The text a model may read: caption, hashtags, title, handle, start of the transcript."""
    inp = row.get("input") or {}
    text = " ".join([inp.get("caption") or "", " ".join(inp.get("hashtags") or []),
                     inp.get("og_title") or "", inp.get("handle") or "", (inp.get("transcript") or "")[:500]])
    return [w for w in re.findall(r"[a-z0-9]{2,}", text.lower()) if w not in _STOP]


class NaiveBayes:
    """Multinomial Naive Bayes with add-one smoothing: counts words per class, nothing more."""

    def fit(self, docs: list[list[str]], ys: list) -> "NaiveBayes":
        self.prior = Counter(ys)
        self.counts: dict = defaultdict(Counter)
        for doc, y in zip(docs, ys):
            self.counts[y].update(doc)
        self.vocab = {w for c in self.counts.values() for w in c}
        self.total = {y: sum(c.values()) for y, c in self.counts.items()}
        return self

    def predict(self, doc: list[str]):
        n, v = sum(self.prior.values()), len(self.vocab) + 1

        def log_p(y):
            return math.log(self.prior[y] / n) + sum(
                math.log((self.counts[y][w] + 1) / (self.total[y] + v)) for w in doc)
        return max(self.prior, key=log_p)


def macro_f1(gold: list, pred: list) -> float:
    f1s = []
    for c in set(gold):
        tp = sum(1 for g, p in zip(gold, pred) if g == p == c)
        fp = sum(1 for g, p in zip(gold, pred) if p == c != g)
        fn = sum(1 for g, p in zip(gold, pred) if g == c != p)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(f1s) / len(f1s)


def run_category(train: list[dict], test: list[dict]) -> dict:
    tr = [r for r in train if r["gold"].get("category")]
    te = [r for r in test if r["gold"].get("category")]
    model = NaiveBayes().fit([words(r) for r in tr], [r["gold"]["category"] for r in tr])
    gold, pred = [r["gold"]["category"] for r in te], [model.predict(words(r)) for r in te]
    return {"category acc": sum(g == p for g, p in zip(gold, pred)) / len(te), "category macro-F1": macro_f1(gold, pred)}


def run_place(train: list[dict], test: list[dict]) -> dict:
    tr = [r for r in train if r["gold"].get("in_catalog") is not None]
    te = [r for r in test if r["gold"].get("in_catalog") is not None]
    model = NaiveBayes().fit([words(r) for r in tr], [bool(r["gold"]["in_catalog"]) for r in tr])
    pairs = [(bool(r["gold"]["in_catalog"]), model.predict(words(r))) for r in te]
    promos = [p for g, p in pairs if not g]
    return {"place acc": sum(g == p for g, p in pairs) / len(pairs),
            "promo rejected": sum(1 for p in promos if not p) / len(promos)}


def run_aliases(train: list[dict], test: list[dict], tmp: Path) -> dict:
    path = tmp / "aliases.json"
    path.write_text(json.dumps(build_aliases(train, "train")), encoding="utf-8", newline="\n")
    normalizer.reload_aliases(path)
    t = score(test, use_llm=False, settings=IngestionSettings())
    return {"venue same-place (aliases)": t.venue_same / t.venue_scored}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repeats", type=int, default=10)
    ap.add_argument("--seed", type=int, default=491)
    args = ap.parse_args()

    rows = [r for r in load_labels() if r.get("gold") and (r.get("verdict") or {}).get("venue") != "needs_review"]
    train = [r for r in rows if split_of(r["url"]) == "train"]
    test = [r for r in rows if split_of(r["url"]) == "test"]
    sizes = [n for n in (50, 100, 150, 200, 250, 300, 350, 400) if n < len(train)] + [len(train)]
    rng = random.Random(args.seed)
    print(f"train pool {len(train)} labelled reels · fixed test split {len(test)} reels · "
          f"{args.repeats} random draws per size\n")

    results: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    with tempfile.TemporaryDirectory() as tmp:
        use_aliases("none")                                  # n=0: the rules with no learned table
        t = score(test, use_llm=False, settings=IngestionSettings())
        results["venue same-place (aliases)"][0].append(t.venue_same / t.venue_scored)
        for n in sizes:
            for _ in range(1 if n == len(train) else args.repeats):
                sample = rng.sample(train, n)
                for out in (run_aliases(sample, test, Path(tmp)), run_category(sample, test), run_place(sample, test)):
                    for metric, value in out.items():
                        results[metric][n].append(value)
        use_aliases("train")

    # What each learner has to beat on the same test split (docs/BASELINE.md).
    reference = {
        "venue same-place (aliases)": "n=0 is the rules alone",
        "category acc": "rules 82.2% · always-majority 60.0%",
        "category macro-F1": "rules 48.2%",
        "place acc": "rules 90.3% · always 'a place' 90.3%",
        "promo rejected": "rules 33.3%",
    }
    for metric, by_n in results.items():
        print(f"{metric}   (to beat: {reference[metric]})")
        points = []
        for n in sorted(by_n):
            vals = by_n[n]
            mean, sd = statistics.mean(vals), (statistics.stdev(vals) if len(vals) > 1 else 0.0)
            points.append((n, mean))
            bar = "█" * round(mean * 40)
            print(f"  n={n:>4}  {100 * mean:5.1f}% ± {100 * sd:4.1f}  {bar}")
        n_now = max(n for n, _ in points)
        b = upper_half_slope(points)
        if b is not None:
            print(f"  upper-half slope {100 * b:+.1f} pts per doubling → +100 labels (~+70 train): "
                  f"{100 * b * math.log2((n_now + 70) / n_now):+.1f} pts")
        print()


def upper_half_slope(points: list[tuple[int, float]]) -> float | None:
    """Least-squares slope of accuracy on log2(n), over the upper half of the sizes."""
    n_max = max(n for n, _ in points)
    fit = [(math.log2(n), m) for n, m in points if n >= n_max / 2]
    if len(fit) < 3:
        return None
    xs, ys = zip(*fit)
    xbar, ybar = statistics.mean(xs), statistics.mean(ys)
    return sum((x - xbar) * (y - ybar) for x, y in fit) / sum((x - xbar) ** 2 for x in xs)


if __name__ == "__main__":
    main()
