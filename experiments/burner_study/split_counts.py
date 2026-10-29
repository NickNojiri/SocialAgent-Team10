"""Counts per split (no scoring, no test labels read): corpus, Nick's confirmed burner reels, silver."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from burner_common import corpus, ev, gold_new, silver_new  # noqa: E402

labeled = [r for r in corpus if (r.get("verdict") or {}).get("venue") != "needs_review"]
for name, rows in (("corpus (gold, fixtures/labels.jsonl)", labeled), ("burner reels Nick confirmed (gold)", gold_new),
                   ("burner reels Claude labeled (silver)", silver_new)):
    c = Counter(ev.split_of(r) for r in rows)
    print(f"{name:40} train {c['train']:4} | dev {c['dev']:4} | test {c['test']:4} | total {sum(c.values())}")
creators = Counter(ev.split_of(r) for r in {ev.creator_of(r): r for r in labeled}.values())
print(f"{'corpus creators':40} train {creators['train']:4} | dev {creators['dev']:4} | test {creators['test']:4}")
