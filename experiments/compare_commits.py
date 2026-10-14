"""Did a change to the extractor really help? Score two commits on the same labels, paired.

    python experiments/compare_commits.py --before bbb74211               # vs the working tree, test split
    python experiments/compare_commits.py --before bbb74211 --after HEAD --split train

Both versions read the *current* fixtures/labels.jsonl and the current train-only alias
table, and both are scored by the *current* scorer (src/ingestion/eval.py), so the only
thing that differs is the extraction code. Each commit runs in a throwaway git worktree.

Reports, per metric: before and after with 95% Wilson intervals, a paired bootstrap
interval on the difference, and McNemar's exact test on the rows that changed. A gain
whose interval crosses zero, or with p >= 0.05, is not evidence.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import (  # noqa: E402
    _SCOREABLE_VERDICTS, _norm, format_rate, load_labels, mcnemar_exact,
    paired_delta_interval, split_of, venue_hit, venue_same_place,
)

# Runs inside a checkout (cwd = that checkout): that checkout's extractor, on whatever
# labels and alias table sit in its fixtures/.
_DUMP = """
import json, sys
from src.ingestion.eval import load_labels, raw_from_input, use_aliases
from src.ingestion.pipeline.normalizer import normalize
use_aliases("train")
out = {}
for r in load_labels():
    c = normalize(raw_from_input(r["url"], r.get("input") or {}))
    cat = c.get("category")
    out[r["url"]] = {"venue": c.get("venue_name"), "category": getattr(cat, "value", cat),
                     "is_vague": bool(c.get("is_vague"))}
sys.stdout.write(json.dumps(out))
"""
_SHARED = ("fixtures/labels.jsonl", "fixtures/venue_aliases.train.json")


def predictions(commit: str | None) -> dict:
    """{url: prediction} from `commit`, or from the working tree when None."""
    if commit is None:
        return _run_dump(REPO)
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--detach", "-q", str(tree), commit],
                       check=True)
        try:
            for rel in _SHARED:                        # same labels, same alias table
                shutil.copyfile(REPO / rel, tree / rel)
            return _run_dump(tree)
        finally:
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(tree)], check=True)


def _run_dump(tree: Path) -> dict:
    done = subprocess.run([sys.executable, "-c", _DUMP], cwd=tree, capture_output=True,
                          text=True, encoding="utf-8", check=True)
    return json.loads(done.stdout)


def paired(rows: list[dict], before: dict, after: dict) -> dict[str, tuple[list[bool], list[bool]]]:
    """Per metric, (before hits, after hits) over the rows the scorecard scores."""
    out: dict[str, tuple[list[bool], list[bool]]] = {
        "venue exact": ([], []), "venue same place": ([], []), "category": ([], []),
        "promo rejected": ([], []), "real place kept": ([], []),
    }
    for row in rows:
        gold, verdict = row.get("gold") or {}, row.get("verdict") or {}
        b, a = before[row["url"]], after[row["url"]]
        if verdict.get("venue") in _SCOREABLE_VERDICTS and (gold.get("venue") or gold.get("in_catalog") is False):
            for name, hit in (("venue exact", lambda p: venue_hit(p, gold.get("venue"))[0]),
                              ("venue same place", lambda p: venue_same_place(p, gold.get("venue")))):
                out[name][0].append(bool(hit(b["venue"])))
                out[name][1].append(bool(hit(a["venue"])))
        if verdict.get("category") in _SCOREABLE_VERDICTS and gold.get("category"):
            out["category"][0].append(_norm(b["category"]) == _norm(gold["category"]))
            out["category"][1].append(_norm(a["category"]) == _norm(gold["category"]))
        if gold.get("in_catalog") is False:
            out["promo rejected"][0].append(b["is_vague"])
            out["promo rejected"][1].append(a["is_vague"])
        elif gold.get("in_catalog") is True:
            out["real place kept"][0].append(not b["is_vague"])
            out["real place kept"][1].append(not a["is_vague"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", required=True, help="commit for the 'before' extractor")
    ap.add_argument("--after", help="commit for the 'after' extractor (default: working tree)")
    ap.add_argument("--split", choices=["train", "test", "all"], default="test")
    args = ap.parse_args()

    rows = [r for r in load_labels() if args.split == "all" or split_of(r["url"]) == args.split]
    before, after = predictions(args.before), predictions(args.after)
    label_commit = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%h", "--", "fixtures/labels.jsonl"],
                                  capture_output=True, text=True).stdout.strip()
    print(f"split={args.split} · {len(rows)} rows · labels as of {label_commit} · "
          f"before={args.before} · after={args.after or 'working tree'}\n")
    for name, (b, a) in paired(rows, before, after).items():
        if not b:
            continue
        b_only, a_only, p = mcnemar_exact(b, a)
        delta = paired_delta_interval(b, a)
        delta_text = "n/a" if delta is None else f"[{delta[0]:+.1%}, {delta[1]:+.1%}]"
        print(f"  {name:<17} before {format_rate(sum(b), len(b))}\n"
              f"  {'':<17} after  {format_rate(sum(a), len(a))}\n"
              f"  {'':<17} change 95% CI {delta_text} · McNemar: {b_only} got worse, "
              f"{a_only} got better, p={p:.4f}\n")


if __name__ == "__main__":
    main()
