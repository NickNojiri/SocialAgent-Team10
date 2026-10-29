"""Test 1: sort a model's dev mistakes into buckets, to see which fix would help most.

Buckets, checked in this order for every reel the model gets wrong (strict scoring):
  near miss                  right place, name a bit off (counts under tolerant scoring)
  named a place, none exists the label says not a place / no single venue
  said none, venue exists    the model picked [NO VENUE] but the reel has one
  wrong pick                 the answer WAS one of the candidates; the model chose another
  missed by extractor        the answer is written in the reel's text but never became a candidate
  in no text                 the answer appears nowhere in caption / speech / screen / tag / handle
Plus a tag on each: whether the poster is the answer (poster = venue).

Reads the cached predictions from compare_models.py (dev only). Writes compare/error_analysis.md.
    python experiments/burner_study/error_analysis.py [arm ...]   (default ft_small_s13 ft_small_s14)
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import compare_models as C  # noqa: E402
import ml_reranker as M  # noqa: E402
from burner_common import squash  # noqa: E402

BUCKETS = ["near miss", "named a place, none exists", "said none, venue exists", "wrong pick",
           "missed by extractor", "in no text"]


def bucket(ex, pred):
    gold = ex.gold
    if M.tolerant(pred, gold):
        return "near miss"
    if not gold:
        return "named a place, none exists"
    in_cands = any(M.tolerant(o, gold) for o in ex.options if o != M.NONE)
    if not pred:
        return "said none, venue exists" + (" (it was a candidate)" if in_cands else "")
    if in_cands:
        return "wrong pick"
    inp = ex.row.get("input") or {}
    text = squash(" ".join(str(inp.get(k) or "") for k in ("caption", "transcript", "ocr", "location_text", "handle", "author_name")))
    return "missed by extractor" if squash(gold) and squash(gold) in text else "in no text"


def poster_is_answer(ex):
    inp = ex.row.get("input") or {}
    g = squash(ex.gold or "")
    return bool(g) and any(g and (g in squash(inp.get(k) or "") or squash(inp.get(k) or "") in g)
                           for k in ("handle", "author_name") if len(squash(inp.get(k) or "")) >= 4)


def main():
    arms = sys.argv[1:] or ["ft_small_s13", "ft_small_s14"]
    exs = C.dev_examples()
    by_code = {C.code(e): e for e in exs}
    lines = [f"# Error analysis: dev mistakes by type ({len(exs)} dev reels)", "",
             "Strict scoring. Buckets are checked in order; see error_analysis.py for definitions.", ""]
    for arm in arms:
        got = C.cache(arm)[1]
        if len(got) < len(exs):
            lines.append(f"## {arm}: not scored yet ({len(got)}/{len(exs)})\n")
            continue
        miss = [(by_code[c], got[c]["pred"]) for c in by_code if not C.same(got[c]["pred"], by_code[c].gold)]
        counts = Counter(bucket(e, p) for e, p in miss)
        poster = Counter(bucket(e, p) for e, p in miss if poster_is_answer(e))
        people = Counter(bucket(e, p) for e, p in miss if e.row.get("labeler") != "claude")
        examples = defaultdict(list)
        for e, p in miss:
            examples[bucket(e, p)].append(f"`{C.code(e)}` said {p!r}, label {e.gold!r}")
        lines += [f"## {arm}: {len(miss)} mistakes of {len(exs)}", "",
                  "| bucket | reels | share | poster is the answer | labeled by people |", "|---|---|---|---|---|"]
        for b, n in counts.most_common():
            lines.append(f"| {b} | {n} | {n / len(miss):.0%} | {poster[b]} | {people[b]} |")
        lines += ["", "Examples (up to 5 per bucket):", ""]
        for b, n in counts.most_common():
            lines.append(f"- **{b}**: " + "; ".join(examples[b][:5]))
        lines.append("")
    text = "\n".join(lines)
    (C.OUT / "error_analysis.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
