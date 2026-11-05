"""M2 place picker: the trained cross-encoder that chooses a reel's venue from candidates.

Research: experiments/burner_study/ml_reranker.py (M2 v5, 5 epochs: 74.2% on 271 dev reels
against the rules' 62.0%; docs/results/2026-10-27-evaluation-report.html). This module runs
that model inside the pipeline:

  1. candidates: the rules' own pick plus every other place name the caption offers
     (mentions, pin lines, "at X", quoted names, location tag, author, hashtags, title-case
     phrases), the same list experiments/ranker.py builds;
  2. one input per reel in the training format (poster, parser pick, recipe cue, location
     tag, caption, speech, on-screen text);
  3. the model scores each candidate and "[NO VENUE]"; the best candidate becomes the venue.

Off unless PLACE_PICKER_MODEL names a saved model folder (data/models/reranker_…) and torch
+ transformers are installed. When it is off, or anything fails, the rules' answer stands.
When the model says "no venue" but the rules found one, the rules' pick is kept, so M2 can
correct a venue but never drops a spot; `--eval` measures exactly this policy.

Differences from training, measured by --eval rather than assumed: no places-lookup or
account-type data at runtime (both lived in the researcher's data/ folder), and the caption
is whitespace-cleaned rather than run through clean_inputs.py.

    python -m src.ingestion.pipeline.place_picker --model data/models/<folder> --split dev
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from src.ingestion.pipeline import normalizer as nz

NONE = "[NO VENUE]"
MAX_CANDS = 12
SLOT = "m2"

_TITLE_PHRASE = re.compile(r"(?<![@#\w])([A-Z][\w'’&.]*(?:\s+(?:&|and|of|de|la|el|the|[A-Z0-9][\w'’&.]*)){1,4})")
_HASHTAG = re.compile(r"#(\w{4,30})")
_GENERIC_TAGS = re.compile(r"^(?:food|foodie|eats|la|socal|oc|yum|reels?|explore|fyp|viral|"
                           r"\w*(?:food|eats|foodie|blogger|life|vibes|gram|things|guide))$", re.I)
_RECIPE_WORDS = re.compile(r"\b(recipe|ingredients?|tbsp|tsp|tablespoons?|teaspoons?|preheat|bake at|"
                           r"\d+\s?(?:g|grams|cups?|oz)\b)", re.I)


def _sq(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _flat(s: Optional[str], n: int) -> str:
    return " ".join((s or "").split())[:n]


@dataclass
class Candidate:
    name: str
    sources: set = field(default_factory=set)
    rules: bool = False


def candidates(raw, rules_pick: Optional[str]) -> list[Candidate]:
    """Every place name the post offers, the rules' pick first (experiments/ranker.py)."""
    caption = raw.caption or ""
    author = (raw.author_handle or "").lstrip("@")
    found: dict[str, Candidate] = {}

    def add(name: Optional[str], source: str, rules: bool = False) -> None:
        name = (name or "").strip(" .,!?:;\"'()[]·-–—")
        key = _sq(name)
        if len(key) < 3 or len(name) > 60:
            return
        c = found.setdefault(key, Candidate(name))
        c.sources.add(source)
        c.rules = c.rules or rules

    add(rules_pick, "rules", rules=True)
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
    add((raw.location_text or "").split(",")[0], "location_tag")
    if author:
        add(nz._clean_display_name(raw.author_name) or nz._handle_to_name(author), "author")
    add(raw.title, "title")
    for tag in _HASHTAG.findall(caption):
        if not _GENERIC_TAGS.match(tag):
            add(nz._handle_to_name(tag), "hashtag")
    for m in _TITLE_PHRASE.finditer(caption[:600]):
        add(m.group(1), "titlecase")
    cands = list(found.values())
    if len(cands) > MAX_CANDS:                     # keep the rules' pick, then the rest in order
        cands = [c for c in cands if c.rules] + [c for c in cands if not c.rules]
        cands = cands[:MAX_CANDS]
    return cands


def context(raw, rules_pick: Optional[str]) -> str:
    """The reel as the model saw it in training (ml_reranker.context, --parser-pick --recipe-cue)."""
    h = (raw.author_handle or "").lstrip("@")
    caption = raw.caption or ""
    parts = [f"poster: @{h}" + (f" ({raw.author_name})" if raw.author_name else ""),
             f"parser pick: {rules_pick or 'none'}"]
    if (nz._RECIPE_RE.search(caption) or nz._RECIPE_CTA.search(caption)
            or len(_RECIPE_WORDS.findall(caption)) >= 2):
        parts.append("post looks like: recipe")
    if raw.location_text:
        parts.append(f"location tag: {raw.location_text}")
    parts.append("caption: " + _flat(caption, 700))
    if raw.transcript:
        parts.append("said in video: " + _flat(raw.transcript, 400))
    if raw.frame_text:
        parts.append("on screen: " + (raw.frame_text or "")[:200])
    return " | ".join(parts)


Scorer = Callable[[Sequence[str], str], Sequence[float]]


class PlacePicker:
    """Chooses among candidates with a scorer: options + context -> one score per option."""

    def __init__(self, scorer: Scorer, name: str = "m2"):
        self.scorer, self.name = scorer, name

    def pick(self, raw, rules_pick: Optional[str]) -> Optional[str]:
        """The model's venue, or None to keep the rules' answer."""
        cands = candidates(raw, rules_pick)
        if not cands:
            return None
        options = [c.name for c in cands] + [NONE]
        scores = list(self.scorer(options, context(raw, rules_pick)))
        best = max(range(len(options)), key=lambda i: scores[i])
        choice = options[best]
        if choice == NONE or (rules_pick and _sq(choice) == _sq(rules_pick)):
            return None
        return choice


def torch_scorer(model_dir: str) -> Scorer:
    """The saved cross-encoder (AutoModelForSequenceClassification, one logit per pair)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, num_labels=1).eval()

    def score(options: Sequence[str], ctx: str) -> list[float]:
        enc = tok(list(options), [ctx] * len(options), truncation="only_second", max_length=256,
                  padding=True, return_tensors="pt")
        with torch.no_grad():
            return model(**enc).logits.squeeze(-1).float().tolist()

    return score


_LOADED: dict = {}


def from_env() -> Optional[PlacePicker]:
    """The picker PLACE_PICKER_MODEL names, loaded once; None when unset or unavailable."""
    path = os.getenv("PLACE_PICKER_MODEL", "").strip()
    if not path:
        return None
    if path not in _LOADED:
        try:
            _LOADED[path] = PlacePicker(torch_scorer(path), name="m2:" + os.path.basename(path.rstrip("/\\")))
        except Exception as exc:                    # missing torch, missing folder: rules only
            print(f"[place_picker] off: couldn't load {path!r}: {exc}")
            _LOADED[path] = None
    return _LOADED[path]


def main(argv: Optional[list[str]] = None) -> int:
    """Measure the product policy on a frozen split: rules alone vs rules + M2."""
    import argparse

    from src.ingestion import eval as ev

    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--model", required=True, help="saved model folder, e.g. data/models/reranker_v5_tune_e5_frac1.0")
    ap.add_argument("--split", default="dev", choices=["train", "dev"], help="never test: that's --final in eval.py")
    args = ap.parse_args(argv)
    picker = PlacePicker(torch_scorer(args.model))
    rows = [r for r in ev.load_labels() if ev.split_of(r) == args.split and (r.get("gold") or {}).get("venue")]
    rules_hits, m2_hits = [], []
    for r in rows:
        raw = ev.raw_from_input(r["url"], r.get("input") or {})
        rules = nz.normalize(raw).get("venue_name")
        final = picker.pick(raw, rules) or rules
        gold = r["gold"]["venue"]
        rules_hits.append(bool(ev.venue_same_place(rules, gold)))
        m2_hits.append(bool(ev.venue_same_place(final, gold)))
    n = len(rows) or 1
    b, c, p = ev.mcnemar_exact(rules_hits, m2_hits)
    print(f"{args.split}: {len(rows)} reels with a venue label")
    print(f"  rules alone   {sum(rules_hits)}/{len(rows)} ({100 * sum(rules_hits) / n:.1f}%)")
    print(f"  rules + M2    {sum(m2_hits)}/{len(rows)} ({100 * sum(m2_hits) / n:.1f}%)   McNemar p={p:.3g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
