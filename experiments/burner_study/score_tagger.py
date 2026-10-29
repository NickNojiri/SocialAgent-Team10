"""Score the saved span tagger (data/models/span_tagger_v0) on the original dev reels without
retraining: parser vs transformer vs 'parser, transformer if empty'. Same scorers as before."""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
os.chdir(REPO)
sys.argv = sys.argv[:1]
import finetune_tagger as F  # noqa: E402
from transformers import AutoModelForTokenClassification, AutoTokenizer  # noqa: E402

from src.ingestion.config import IngestionSettings  # noqa: E402

ev = F.ev
path = REPO / "data" / "models" / "span_tagger_v0"
model = AutoModelForTokenClassification.from_pretrained(path).eval()
tok = AutoTokenizer.from_pretrained(path)
ev.use_aliases("fit")
dev = [r for r in F.corpus if ev.split_of(r) == "dev"]
parser = {r["code"]: r for r in ev.score(dev, use_llm=False, settings=IngestionSettings()).rows}
res = dict.fromkeys(["parser", "transformer", "parser, transformer if empty"], 0)
tol = dict.fromkeys(res, 0)
n = 0
for row in dev:
    g, v = row.get("gold", {}), row.get("verdict", {})
    if not (v.get("venue") in ev._SCOREABLE_VERDICTS and (g.get("venue") or g.get("in_catalog") is False)):
        continue
    n += 1
    pv = parser[ev._code(row["url"])]["pred_venue"]
    mv, _ = F.predict(model, tok, (row.get("input") or {}).get("caption") or "", 192)
    for k, p in (("parser", pv), ("transformer", mv), ("parser, transformer if empty", pv or mv)):
        res[k] += ev.venue_same_place(p, g.get("venue"))
        tol[k] += F.tolerant(p, g.get("venue"))
print(f"dev reels {n}")
for k in res:
    print(f"  {k:30} same place {ev.format_rate(res[k], n)}   tolerant {tol[k]}/{n}")
