"""Step 0: write the unlabeled caption corpus for self-supervised pretraining.

Uses captions + transcripts + on-screen text from fixtures/labels.jsonl, TRAIN split
only: a test caption the model has read during pretraining is no longer held out.

    python experiments/ssl/export_text.py            -> data/ssl/corpus.txt
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.ingestion.eval import LABELS_PATH, split_of  # noqa: E402

OUT = REPO / "data" / "ssl" / "corpus.txt"


def texts(rows: list[dict]) -> list[str]:
    out = []
    for r in rows:
        if split_of(r["url"]) != "train":
            continue
        inp = r.get("input") or {}
        for field in ("caption", "transcript", "ocr"):
            t = re.sub(r"\s+", " ", inp.get(field) or "").strip()
            if len(t) >= 20:                      # skip "Music", "You", one-word noise
                out.append(t)
    return out


def main() -> None:
    rows = [json.loads(l) for l in LABELS_PATH.read_text(encoding="utf-8").split("\n") if l.strip()]
    lines = texts(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(lines)} passages ({sum(map(len, lines)):,} chars) -> {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
