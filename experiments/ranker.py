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
                   poster, a city, a business word, how often it appears...), plus whether
                   the poster sounds like the place itself (our/us, hours, ordering, menu).
                   none_features() describes the extra "no venue" option (a list, a recipe,
                   an ad, the rules found nothing).
  3. train()       a softmax over each reel's options, plain Python, L2-regularized: the
                   candidates and "no venue" compete, and the right one should win.
  4. pick()        the option with the highest probability. The L2 strength is chosen by
                   5-fold cross-validation on train, grouped by creator.

Rules of the road (docs/DATA_PLAN.md): it learns from train only, is compared on dev,
and never reads test unless --split test --final "why".

Results so far (dev, paired with the rules on the same 106 reels):
  v1, 2026-10-20   63.2% vs rules 65.1% (-1.9 pts, p=0.5): a tie. It learned to trust the
                   rules' pick. The candidates hold the answer for 85.8%, so the room is real.
  + venue list     no change. Only 16% of gold venues are on the LA/OC OpenStreetMap list
                   (24% with a loose match): much of the corpus is New York, San Diego,
                   Seoul... The list helps a SoCal-only product, not this corpus.
  v2, 2026-10-20   softmax per reel + a "no venue" option + poster-is-the-place features:
                   dev 65.1% vs rules 65.1% (+0.0 pts, CI [-2.8, +2.8], 1 win each, p=1):
                   still a tie. Train 79.3% vs 77.1% (+2.2, CI [+0.3, +4.1], p=0.07), and
                   5-fold CV on train 77.7%: the gain doesn't carry to new creators. Other
                   epochs / learning rates / L2 all land at 77.4-78.1% CV (noise).
  Next: the reachable misses need signals the caption text doesn't carry (the account's
  bio and category, which capture doesn't record), or more train reels of that kind.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
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


# A business talking about itself: ordering, booking, menu and price talk. Hours, phone
# numbers and addresses are already in normalizer._BIZ_DETAIL; "our/us" in _OWN_VOICE.
_ORDER_CUE = re.compile(r"\b(?:order (?:now|online|ahead)|dm (?:us )?to order|pre-?order|reserve|"
                        r"reservations?|book (?:now|a table|online)|link in (?:our )?bio|we(?:'| a)re open)\b", re.I)
_MENU_CUE = re.compile(r"\$\s?\d|\bnew (?:on|to) (?:the|our) menu\b|\bmenu\b|\bspecials?\b", re.I)


def poster_signals(row: dict) -> dict[str, float]:
    """Is the poster the place (a business posting about itself) or a reviewer?"""
    inp = row.get("input") or {}
    caption = inp.get("caption") or ""
    handle = (inp.get("handle") or "").lstrip("@").lower()
    return {
        "biz_account": float(nz._is_business_account(caption, handle)),
        "biz_handle": float(nz._biz_handle(handle)),
        "biz_detail": float(bool(nz._BIZ_DETAIL.search(caption))),
        "own_voice": float(bool(nz._OWN_VOICE.search(caption))),
        "order_cue": float(bool(_ORDER_CUE.search(caption))),
        "menu_cue": float(bool(_MENU_CUE.search(caption))),
        "blogger": float(bool(nz._BLOGGER_HANDLE.search(handle))),
    }


def features(c: Candidate, row: dict, rules_none: bool = False) -> dict[str, float]:
    inp = row.get("input") or {}
    caption = inp.get("caption") or ""
    key, flat_caption = sq(c.name), sq(caption)
    author = sq(inp.get("handle"))
    words = c.name.split()
    first = caption.lower().find(c.name.lower())
    is_poster = bool(author) and (key == author or (len(key) > 4 and (key in author or author in key)))
    f = {f"src_{s}": float(s in c.sources) for s in SOURCES}
    f.update({
        "n_sources": min(len(c.sources), 4) / 4,
        "rules_strong_slot": float(c.rules_slot in STRONG_SLOTS),
        "rules_weak_slot": float(bool(c.rules_slot) and c.rules_slot not in STRONG_SLOTS),
        "is_poster": float(is_poster),
        "is_display_name": float(bool(key) and key == sq(inp.get("author_name"))),
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
    })
    # Softmax compares candidates within one reel, so a fact about the whole post (the
    # poster sounds like a business) only matters crossed with a candidate: it should lift
    # the poster's own name, and nothing else.
    own = float(is_poster or f["is_display_name"] or "author" in c.sources)
    for k, v in poster_signals(row).items():
        f[f"poster_{k}"] = own * v
    none = float(rules_none)
    for k in ("src_mention", "src_pin", "src_location_tag", "src_author", "src_at", "src_titlecase",
              "is_poster", "business_word", "early"):
        f[f"{k}_when_rules_none"] = f[k] * none
    return f


def none_features(row: dict, out: dict) -> dict[str, float]:
    """The "no venue" option: why this post might name no place at all."""
    inp = row.get("input") or {}
    caption = inp.get("caption") or ""
    return {
        "none": 1.0,
        "none_rules_none": float(not out.get("venue_name")),
        "none_rules_vague": float(bool(out.get("is_vague"))),
        "none_post_is_list": float(nz._looks_like_list(raw_from_input(row["url"], inp), caption)),
        "none_recipe": float(bool(nz._RECIPE_RE.search(caption) or nz._RECIPE_CTA.search(caption))),
        "none_ad": float(bool(nz._AD_MARKERS.search(caption))),
    }


FEATURES = (sorted(features(Candidate("Sample Cafe", {"rules"}), {"url": "u", "input": {}}))
            + sorted(none_features({"url": "u", "input": {}}, {})))


def vector(f: dict[str, float]) -> list[tuple[int, float]]:
    """Sparse: (feature index, value) for the non-zero features only."""
    return [(i, f[k]) for i, k in enumerate(FEATURES) if f.get(k)]


# ── 3. softmax over each reel's options, plain Python ──────────────────────────
#
# Each reel offers its candidates plus one "no venue" option. The model gives each a score
# (weights · features) and turns the scores into probabilities that sum to 1 within the
# reel, so candidates compete with each other instead of each being judged alone.

def _score(w: list[float], x: list[tuple[int, float]]) -> float:
    return sum(w[i] * v for i, v in x)


def probs(w: list[float], options: list[list[tuple[int, float]]]) -> list[float]:
    scores = [_score(w, x) for x in options]
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def train(reels: list["Reel"], l2: float = 0.01, epochs: int = 200, lr: float = 0.5) -> list[float]:
    """Weights minimizing the average −log P(right option) + l2·|w|². Reels whose answer
    is not among the candidates teach nothing and are skipped."""
    w = [0.0] * len(FEATURES)
    usable = [r for r in reels if r.target >= 0]
    for _ in range(epochs):
        grad = [0.0] * len(w)
        for r in usable:
            options = r.options()
            for j, (x, p) in enumerate(zip(options, probs(w, options))):
                err = p - (j == r.target)
                for i, v in x:
                    grad[i] += err * v
        for i in range(len(w)):
            w[i] -= lr * (grad[i] / max(len(usable), 1) + 2 * l2 * w[i])
    return w


# ── 4. the reel-level pipeline ─────────────────────────────────────────────────

@dataclass
class Reel:
    row: dict
    gold: str | None
    rules_pred: str | None
    cands: list[Candidate]
    xs: list[list[tuple[int, float]]]
    none_x: list[tuple[int, float]]
    target: int = -1          # index of the right option (len(cands) = no venue); -1: not reachable

    def options(self) -> list[list[tuple[int, float]]]:
        return self.xs + [self.none_x]


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
        gold = (r.get("gold") or {}).get("venue")
        reel = Reel(r, gold, out.get("venue_name"), cands,
                    [vector(features(c, r, none)) for c in cands], vector(none_features(r, out)))
        if not gold:
            reel.target = len(cands)
        else:
            reel.target = next((j for j, c in enumerate(cands) if venue_same_place(c.name, gold)), -1)
        reels.append(reel)
    return reels


def pick(w: list[float], reel: Reel) -> str | None:
    """The most likely option; None when "no venue" wins."""
    p = probs(w, reel.options())
    best = max(range(len(p)), key=p.__getitem__)
    return reel.cands[best].name if best < len(reel.cands) else None


def hits(preds: list[str | None], reels: list[Reel]) -> list[bool]:
    return [venue_same_place(p, r.gold) for p, r in zip(preds, reels)]


L2S = (0.001, 0.01, 0.1)


def choose(reels: list[Reel], folds: int = 5, seed: int = 491) -> tuple[float, float]:
    """(l2, CV accuracy) by k-fold cross-validation over train, grouped by creator so a
    fold never sees a creator it is scored on."""
    groups = sorted({creator_of(r.row) for r in reels})
    random.Random(seed).shuffle(groups)
    fold_of = {g: i % folds for i, g in enumerate(groups)}
    best = (L2S[0], -1.0)
    for l2 in L2S:
        right = 0
        for k in range(folds):
            fit = [r for r in reels if fold_of[creator_of(r.row)] != k]
            held = [r for r in reels if fold_of[creator_of(r.row)] == k]
            w = train(fit, l2=l2)
            right += sum(hits([pick(w, r) for r in held], held))
        if right / len(reels) > best[1]:
            best = (l2, right / len(reels))
    return best


def oracle(reels: list[Reel]) -> int:
    """Reels where some option is right (a candidate, or "no venue"): the ranker's ceiling."""
    return sum(1 for r in reels if r.target >= 0)


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

    l2, cv = choose(train_reels)
    w = train(train_reels, l2=l2)
    print(f"ranker v2 (softmax per reel): {len(FEATURES)} features · l2={l2} "
          f"(5-fold CV on train, by creator: {cv:.1%})"
          + ("" if _VENUES else " · no venue list yet (scripts/build_venue_list.py)") + "\n")

    for name, reels in (("train", train_reels), (args.split, held)):
        rules = hits([r.rules_pred for r in reels], reels)
        ranker = hits([pick(w, r) for r in reels], reels)
        b_only, a_only, p = mcnemar_exact(rules, ranker)
        ci = paired_delta_interval(rules, ranker)
        print(f"{name:>5}  {len(reels)} reels · an option holds the answer for "
              f"{format_rate(oracle(reels), len(reels))}")
        print(f"       rules  : {format_rate(sum(rules), len(rules))}")
        print(f"       ranker : {format_rate(sum(ranker), len(ranker))}")
        delta = sum(ranker) - sum(rules)
        print(f"       change : {delta / len(reels):+.1%}"
              + (f" CI [{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "")
              + f" · rules only {b_only}, ranker only {a_only}, McNemar p={p:.3g}\n")

    if args.weights:
        print("learned weights (positive = makes that option more likely to win):")
        for k, v in sorted(zip(FEATURES, w), key=lambda kv: -abs(kv[1])):
            print(f"  {v:+7.2f}  {k}")
        print()

    if args.show:
        shown = 0
        for r in held:
            ours = pick(w, r)
            if bool(venue_same_place(ours, r.gold)) == bool(venue_same_place(r.rules_pred, r.gold)):
                continue
            print(f"  gold={r.gold!r}  rules={r.rules_pred!r}  ranker={ours!r}  {r.row['url']}")
            shown += 1
            if shown >= args.show:
                break
    return 0


if __name__ == "__main__":
    sys.exit(main())
