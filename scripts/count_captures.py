"""How many captured reels are there to label? Counts only — safe to paste anywhere.

    python scripts/count_captures.py          # on any laptop that has run the bot or the DM watcher
    python scripts/count_captures.py --json   # the same numbers, one line, to send to Nick

Looks in the places captures land: data/inspirations/<server>.jsonl (the bot),
data/inspirations.jsonl (older shared file), fixtures/labels.inbox.jsonl (the DM watcher)
and fixtures/labels.batch*.jsonl. A reel already in fixtures/labels.jsonl counts as
labeled. It prints no caption, no link and no server id: those stay on your machine
(docs/DATA_PLAN.md).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import LABELS_PATH, _code, split_of  # noqa: E402


def sources(repo: Path) -> dict[str, list[Path]]:
    return {
        "bot captures": sorted((repo / "data" / "inspirations").glob("*.jsonl"))
        + [p for p in [repo / "data" / "inspirations.jsonl"] if p.exists()],
        "DM watcher inbox": [p for p in [repo / "fixtures" / "labels.inbox.jsonl"] if p.exists()],
        "label batches": sorted((repo / "fixtures").glob("labels.batch*.jsonl")),
    }


def _reel(line: str) -> tuple[str, str] | None:
    """(url, handle) from a capture record or a label-shaped row, or None."""
    try:
        row = json.loads(line)
    except ValueError:
        return None
    if not isinstance(row, dict):
        return None
    prov = row.get("provenance") or {}
    url = row.get("url") or prov.get("source_url") or row.get("source_url")
    handle = (row.get("input") or {}).get("handle") or prov.get("author_handle") or ""
    return (str(url), str(handle)) if url else None


def platform(url: str) -> str:
    return "tiktok" if "tiktok.com" in url else "instagram" if "instagram.com" in url else "other"


def count(repo: Path = REPO, labels: Path = LABELS_PATH) -> dict:
    labeled = {_code(json.loads(line)["url"]) for line in labels.read_text(encoding="utf-8").split("\n")
               if line.strip()} if labels.exists() else set()
    unlabeled: dict[str, tuple[str, str]] = {}
    per_source = {}
    for name, paths in sources(repo).items():
        seen = 0
        for path in paths:
            for line in path.read_text(encoding="utf-8").split("\n"):
                reel = line.strip() and _reel(line)
                if not reel:
                    continue
                seen += 1
                code = _code(reel[0])
                if code not in labeled:
                    unlabeled.setdefault(code, reel)
        per_source[name] = {"files": len(paths), "reels": seen}
    where = Counter(split_of({"url": url, "input": {"handle": handle}}) for url, handle in unlabeled.values())
    return {
        "labeled": len(labeled),
        "sources": per_source,
        "unlabeled_unique": len(unlabeled),
        "unlabeled_by_platform": dict(Counter(platform(url) for url, _ in unlabeled.values())),
        "would_land_in": {s: where[s] for s in ("train", "dev", "test")},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    c = count(REPO, LABELS_PATH)
    if args.json:
        print(json.dumps(c, sort_keys=True))
        return 0
    print(f"labeled already: {c['labeled']} reels (fixtures/labels.jsonl)\n")
    for name, s in c["sources"].items():
        print(f"  {name:<18}{s['files']:>3} files  {s['reels']:>5} reels")
    print(f"\nnot labeled yet: {c['unlabeled_unique']} unique reels  "
          + "  ".join(f"{k} {v}" for k, v in sorted(c["unlabeled_by_platform"].items())))
    w = c["would_land_in"]
    print(f"once labeled they'd split about (bot captures don't keep the poster, so this is by link): train {w['train']} · dev {w['dev']} · test {w['test']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
