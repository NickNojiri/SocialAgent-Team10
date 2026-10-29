"""Alias 'training' test (hypothesis: data/experiments/alias_test_hypothesis.md).

Builds the train-only alias table three ways and scores the SAME original dev reels with each:
  A  today's corpus
  B  + Nick's confirmed burner reels (gold)
  C  + every other burner reel as silver (train-only)
Writes nothing in fixtures/. Prints counts and shortcodes only.
"""
import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.chdir(REPO)
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
from build_aliases import build  # noqa: E402
from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline import normalizer  # noqa: E402

HERE = Path(__file__).parent
DATA = REPO / "data"
norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())


def lines(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").split("\n") if l.strip()]


corpus = ev.load_labels()
inbox = lines(REPO / "fixtures" / "labels.inbox.jsonl")
first = {r["order"]: r for r in json.loads((DATA / "dm_firstpass.json").read_text(encoding="utf-8"))}
decs = {}
for p in (DATA / "experiments" / "review_decisions").glob("*.json"):
    d = json.loads(p.read_text(encoding="utf-8"))
    decs[p.stem] = d.get("data", d)


def verdict(pred: dict, gold: dict) -> dict:
    pv, gv = pred.get("venue"), gold.get("venue")
    pc, gc = pred.get("city"), gold.get("city")
    return {
        "venue": "right" if norm(pv) == norm(gv) else ("missing" if not pv else "wrong"),
        "city": "right" if norm(pc) == norm(gc) else ("missing" if not pc else "wrong"),
        "category": "right" if pred.get("category") == gold.get("category") else "wrong",
    }


gold_new, silver_new = [], []
for i, row in enumerate(inbox):
    f = first[i]
    d = decs.get(f["code"])
    if d:
        v = d.get("verdict")
        g = {"venue": d.get("venue") if v in ("ok", "fixed") else None, "city": d.get("city"),
             "category": d.get("category"), "in_catalog": v not in ("none", "list")}
        extra = {"labeler": "nick"}
    else:
        g = {"venue": f["venue"] if f["kind"] == "place" else None, "city": f["city"],
             "category": f["category"], "in_catalog": f["kind"] not in ("none", "list")}
        extra = {"labeler": "claude", "source": "silver"}
    new = {**row, "gold": g, "verdict": verdict(row["predicted"], g), **extra}
    (gold_new if d else silver_new).append(new)

arms = {"A baseline": corpus, "B +gold": corpus + gold_new, "C +gold+silver": corpus + gold_new + silver_new}
dev = [r for r in corpus if ev.split_of(r) == "dev"]          # the SAME original dev reels
settings = IngestionSettings()
print(f"original dev reels: {len(dev)} | new gold {len(gold_new)} "
      f"(splits {dict(Counter(ev.split_of(r) for r in gold_new))}) | new silver {len(silver_new)}")
for name, rows in arms.items():
    print(f"  {name}: train rows feeding aliases = {sum(ev.split_of(r) == 'train' for r in rows)}")

results = {}
for name, rows in arms.items():
    table = build(rows, "train")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(table, fh, ensure_ascii=False)
    normalizer.reload_aliases(fh.name)
    t = ev.score(dev, use_llm=False, settings=settings)
    results[name] = (t, table)
    os.unlink(fh.name)
normalizer.reload_aliases()

print()
for name, (t, table) in results.items():
    print(f"{name:16} aliases {len(table):4} | venue exact {ev.format_rate(t.venue_exact, t.venue_scored)}"
          f" | same place {ev.format_rate(t.venue_same, t.venue_scored)}"
          f" | category {ev.format_rate(t.cat_hit, t.cat_scored)} | city {ev.format_rate(t.city_hit, t.city_scored)}")

base = {r["code"]: r for r in results["A baseline"][0].rows}
for name in ("B +gold", "C +gold+silver"):
    t = results[name][0]
    pairs = [(base[r["code"]]["venue"], r["venue"]) for r in t.rows if r["venue"] is not None]
    b, a = [x for x, _ in pairs], [y for _, y in pairs]
    gained, lost, p = ev.mcnemar_exact(b, a)
    flips = [(r["code"], base[r["code"]]["pred_venue"], r["pred_venue"], r["gold_venue"])
             for r in t.rows if r["venue"] is not None and (r["venue"] != base[r["code"]]["venue"]
                                                            or r["venue_same"] != base[r["code"]]["venue_same"])]
    print(f"\n{name} vs A (venue exact, paired): fixed {gained}, broke {lost}, McNemar p = {p:.3f}")
    for code, was, now, gold in flips:
        print(f"   {code}: {was!r} -> {now!r}   (gold {gold!r})")
