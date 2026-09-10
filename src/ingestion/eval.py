"""Score the field extractor against the labeled corpus (`fixtures/labels.jsonl`).

    python -m src.ingestion.eval               # heuristic baseline + LLM path if Ollama is up
    python -m src.ingestion.eval --offline     # heuristic baseline only (no network)
    python -m src.ingestion.eval --model llama3.2

The corpus is the single source of truth for extraction accuracy — see
docs/EXTRACTION_ACCURACY.md ("the label loop"). Each row carries the input the
extractor saw, what it `predicted` at capture time, and the human `gold` +
`verdict`. This runs the *current* extractor over every input and reports how
close it gets now, so a prompt/regex/model change has a number to move.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.normalizer import build_llm_payload, normalize
from src.ingestion.schemas.snapshot import RawPostSnapshot

LABELS_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "labels.jsonl"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def split_of(url: str) -> str:
    """Deterministic 70/30 train/test by URL hash — stable as the corpus grows,
    so rules are tuned on `train` and the honest number is `test`."""
    import hashlib

    code = url.rstrip("/").split("/")[-1]
    h = int(hashlib.md5(code.encode()).hexdigest()[:8], 16)
    return "test" if h % 100 < 30 else "train"
_SCOREABLE_VERDICTS = {"right", "wrong", "missing"}   # 'needs_review' is excluded from pass/fail


# ── corpus ────────────────────────────────────────────────────────────────────


def load_labels(path: Path = LABELS_PATH) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"no label corpus at {path} — see docs/EXTRACTION_ACCURACY.md")
    rows = []
    # split on \n only — str.splitlines() also breaks on U+2028/U+2029, which
    # appear inside caption text and would shred a JSONL row.
    for i, line in enumerate(path.read_text().split("\n"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path}:{i}: bad JSON — {exc}")
    return rows


def raw_from_input(url: str, inp: dict) -> RawPostSnapshot:
    """Reconstruct the extractor's input from a label row."""
    return RawPostSnapshot(
        source_url=url,
        platform="instagram",
        extractor="eval",
        caption=inp.get("caption") or None,
        title=inp.get("og_title") or None,
        author_handle=inp.get("handle") or None,
        author_name=inp.get("author_name") or None,
        frame_text=inp.get("ocr") or None,
        transcript=inp.get("transcript") or None,
        hashtags=list(inp.get("hashtags") or []),
        venue_candidate=inp.get("venue_candidate") or None,
        location_text=inp.get("location_text") or None,
        lat=inp.get("lat"),
        lng=inp.get("lng"),
    )


# ── scoring ───────────────────────────────────────────────────────────────────


def _norm(s: Optional[str]) -> str:
    return _NON_ALNUM.sub(" ", (s or "").lower()).strip()


def _tokens(s: Optional[str]) -> set[str]:
    return {t for t in _norm(s).split() if t}


def venue_hit(pred: Optional[str], gold: Optional[str]) -> tuple[bool, bool]:
    """(exact, fuzzy) — fuzzy = Jaccard token overlap >= 0.6."""
    if not gold:
        return (not pred, not pred)          # gold expects no venue → pred should be empty
    if not pred:
        return (False, False)
    if _norm(pred) == _norm(gold):
        return (True, True)
    a, b = _tokens(pred), _tokens(gold)
    jac = len(a & b) / len(a | b) if (a or b) else 0.0
    return (False, jac >= 0.6)


def city_hit(candidate_place_text: str, gold_city: Optional[str]) -> Optional[bool]:
    """None → not scored (gold has no city). Else: gold city appears in the record's geo text."""
    if not gold_city:
        return None
    return _norm(gold_city) in _norm(candidate_place_text)


@dataclass
class Tally:
    n: int = 0
    venue_exact: int = 0
    venue_fuzzy: int = 0
    venue_scored: int = 0
    cat_hit: int = 0
    cat_scored: int = 0
    city_hit: int = 0
    city_scored: int = 0
    vague_hit: int = 0
    vague_scored: int = 0
    # is_vague false positives: gold says this IS a place and we rejected it. The
    # recall line alone hides the cost of rejecting more aggressively.
    vague_fp: int = 0
    vague_kept: int = 0
    # LLM-targeting view: venue rows below the confidence bar, and how many of
    # those are currently wrong (that wrong slice is the LLM's addressable upside).
    lowconf: int = 0
    lowconf_wrong: int = 0
    rows: list[dict] = field(default_factory=list)

    LOWCONF_BAR = 0.6

    def pct(self, num: int, den: int) -> str:
        return f"{(100 * num / den):5.1f}%  ({num}/{den})" if den else "   n/a"

    def report(self, title: str) -> str:
        L = [
            f"── {title} " + "─" * max(0, 60 - len(title)),
            f"  rows scored       : {self.n}",
            f"  venue exact       : {self.pct(self.venue_exact, self.venue_scored)}",
            f"  venue fuzzy (>=.6): {self.pct(self.venue_fuzzy, self.venue_scored)}",
            f"  category          : {self.pct(self.cat_hit, self.cat_scored)}",
            f"  city in geo text  : {self.pct(self.city_hit, self.city_scored)}",
            f"  promo rejected    : {self.pct(self.vague_hit, self.vague_scored)}  (recall — gold in_catalog=false → is_vague)",
            f"  ...its precision  : {self.pct(self.vague_hit, self.vague_hit + self.vague_fp)}  "
            f"({self.vague_fp} real place{'' if self.vague_fp == 1 else 's'} wrongly rejected)",
            f"  low-conf venue    : {self.lowconf} rows < {self.LOWCONF_BAR}  "
            f"({self.lowconf_wrong} wrong → the LLM's addressable upside)",
        ]
        return "\n".join(L)

    def table(self) -> str:
        head = f"  {'venue':<5} {'cat':<3} {'city':<4} {'conf':>4}  {'slot':<13} shortcode      pred venue -> gold venue"
        lines = [head, "  " + "-" * 88]
        for r in self.rows:
            def mk(x):  # noqa: E306
                return {True: " ok ", False: "MISS", None: "  · "}[x]
            lines.append(
                f"  {mk(r['venue'])} {mk(r['cat'])} {mk(r['city'])} {r['conf']:>4.2f}  "
                f"{r['slot']:<13} {r['code']:<14} {r['pred_venue']!r} -> {r['gold_venue']!r}"
            )
        return "\n".join(lines)


def score(rows: list[dict], *, use_llm: bool, settings: IngestionSettings) -> Tally:
    extractor = None
    if use_llm:
        from src.ingestion.pipeline.llm_extractor import LlmFieldExtractor

        extractor = LlmFieldExtractor(settings)

    t = Tally()
    for row in rows:
        gold, verdict = row.get("gold", {}), row.get("verdict", {})
        raw = raw_from_input(row["url"], row.get("input", {}))

        llm_ext = extractor.extract(build_llm_payload(raw)) if extractor else None
        cand = normalize(raw, llm_extraction=llm_ext)

        pred_venue = cand.get("venue_name")
        geo = cand.get("geo")
        geo_text = " ".join(
            filter(None, [getattr(geo, "raw_location_text", None), *(getattr(geo, "place_names", []) or [])])
        )
        pred_cat = getattr(cand.get("category"), "value", cand.get("category"))
        is_vague = bool(cand.get("is_vague") or getattr(llm_ext, "is_vague", False))

        t.n += 1
        code = row["url"].rstrip("/").split("/")[-1]

        v_exact = v_fuzzy = None
        if verdict.get("venue") in _SCOREABLE_VERDICTS and (gold.get("venue") or gold.get("in_catalog") is False):
            v_exact, v_fuzzy = venue_hit(pred_venue, gold.get("venue"))
            t.venue_scored += 1
            t.venue_exact += int(v_exact)
            t.venue_fuzzy += int(v_fuzzy)

        c_hit = None
        if verdict.get("category") in _SCOREABLE_VERDICTS and gold.get("category"):
            c_hit = _norm(pred_cat) == _norm(gold["category"])
            t.cat_scored += 1
            t.cat_hit += int(c_hit)

        city_ok = city_hit(geo_text, gold.get("city"))
        if city_ok is not None:
            t.city_scored += 1
            t.city_hit += int(city_ok)

        if gold.get("in_catalog") is False:
            t.vague_scored += 1
            t.vague_hit += int(is_vague)
        elif gold.get("in_catalog") is True:
            t.vague_kept += 1
            t.vague_fp += int(is_vague)

        conf = float(cand.get("venue_confidence") or 0.0)
        slot = cand.get("venue_slot") or "none"
        if pred_venue and conf < t.LOWCONF_BAR:
            t.lowconf += 1
            if v_exact is False:
                t.lowconf_wrong += 1

        t.rows.append(
            {
                "code": code,
                "venue": v_exact,
                "cat": c_hit,
                "city": city_ok,
                "conf": conf,
                "slot": slot,
                "pred_venue": pred_venue,
                "gold_venue": gold.get("venue"),
            }
        )
    return t


# ── geocode stage (isolated: fed gold venue + city, not extractor output) ─────


def score_geocode(rows: list[dict]) -> None:
    """Answer 'is geo starved or broken?' — feed Nominatim the *correct* venue and
    city from `gold` and see which query shape resolves. Needs network (not IG)."""
    from src.services.location_service import address_to_coords

    cache: dict[str, tuple] = {}

    def geo(q: str):
        if q not in cache:
            cache[q] = address_to_coords(q)
        return cache[q]

    variants = {"venue only": 0, "venue + city": 0, "city only": 0}
    scored = {"venue only": 0, "venue + city": 0, "city only": 0}
    print("── geocode stage (gold venue/city → Nominatim) " + "─" * 16)
    print(f"  {'venue':<5} {'v+cty':<6} {'city':<5}  gold venue / city")
    print("  " + "-" * 60)
    for row in rows:
        g = row.get("gold", {})
        venue, city = g.get("venue"), g.get("city")
        if not venue and not city:
            continue
        hits = {}
        if venue:
            scored["venue only"] += 1
            lat, _ = geo(venue)
            hits["venue only"] = lat is not None
            variants["venue only"] += int(hits["venue only"])
        if venue and city:
            scored["venue + city"] += 1
            lat, _ = geo(f"{venue}, {city}")
            hits["venue + city"] = lat is not None
            variants["venue + city"] += int(hits["venue + city"])
        if city:
            scored["city only"] += 1
            lat, _ = geo(city)
            hits["city only"] = lat is not None
            variants["city only"] += int(hits["city only"])

        def mk(k):  # noqa: E306
            return {True: " ok ", False: "MISS", None: "  · "}[hits.get(k)]

        print(f"  {mk('venue only')} {mk('venue + city')}  {mk('city only')}  {venue!r} / {city!r}")

    print()
    for k in variants:
        den = scored[k]
        pct = f"{100 * variants[k] / den:5.1f}%  ({variants[k]}/{den})" if den else "   n/a"
        print(f"  {k:<13}: {pct}")


# ── cli ───────────────────────────────────────────────────────────────────────


def use_aliases(which: str) -> int:
    """Point the extractor's learned-correction table at 'all' | 'train' | 'none'."""
    from src.ingestion.pipeline import normalizer

    paths = {
        "all": LABELS_PATH.parent / "venue_aliases.json",
        "train": LABELS_PATH.parent / "venue_aliases.train.json",
        "none": LABELS_PATH.parent / "does-not-exist.json",
    }
    return normalizer.reload_aliases(paths[which])


def _ollama_up(settings: IngestionSettings) -> bool:
    import httpx

    try:
        httpx.get(f"{settings.ollama_url}/api/tags", timeout=2.0)
        return True
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="heuristic baseline only, no network")
    ap.add_argument("--model", help="override ollama_model (e.g. llama3.2)")
    ap.add_argument("--table", action="store_true", help="print the per-row table")
    ap.add_argument("--stage", choices=["extract", "geocode"], default="extract",
                    help="which pipeline stage to score (default: extract)")
    ap.add_argument("--split", choices=["all", "train", "test"], default="all",
                    help="score only this split (deterministic 70/30 by URL hash)")
    ap.add_argument("--aliases", choices=["all", "train", "none"],
                    help="which learned-correction table the extractor may use. Default: "
                         "'train' when --split test (so a test row's own gold label is not "
                         "compiled into the extractor scoring it), 'all' otherwise.")
    args = ap.parse_args()

    import os

    settings = IngestionSettings(
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=args.model or os.getenv("OLLAMA_MODEL", "llama3.2"),
    )
    which = args.aliases or ("train" if args.split == "test" else "all")
    n_aliases = use_aliases(which)

    rows = load_labels()
    if args.split != "all":
        rows = [r for r in rows if split_of(r["url"]) == args.split]
    print(f"corpus: {len(rows)} labeled rows  ({LABELS_PATH}, split={args.split})")
    print(f"aliases: {which} ({n_aliases} overrides)"
          + ("   <- held-out: no test row contributes an override" if which == "train" else "")
          + ("   <- includes test rows; NOT a held-out number" if which == "all" and args.split == "test" else "")
          + "\n")

    if args.stage == "geocode":
        score_geocode(rows)
        return

    baseline = score(rows, use_llm=False, settings=settings)
    print(baseline.report(f"heuristic baseline — {args.split}"))
    if args.table:
        print(baseline.table())

    if not args.offline and _ollama_up(settings):
        print()
        llm = score(rows, use_llm=True, settings=settings)
        print(llm.report(f"heuristic + LLM ({settings.ollama_model})"))
        if args.table:
            print(llm.table())
    elif not args.offline:
        print("\n(ollama unreachable — skipped the LLM path; run with it up for the full picture)")


if __name__ == "__main__":
    main()
