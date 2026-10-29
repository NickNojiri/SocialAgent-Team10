"""Ablation: does fine-tuning matter? Score the dev set with the PRETRAINED cross-encoder (the
exact checkpoint M2 starts from, never trained on our data) using M2 v3's inputs and candidates,
then compare with the fine-tuned M2 v3. Same reels, same candidates, only the weights differ."""
import sys
from pathlib import Path

from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev  # noqa: E402

M.OPTS.update(clean=True, parser_pick=True, accounts=True)
dev = [r for r in corpus if ev.split_of(r) == "dev" and M.V.scoreable(r)]
exs = M.build(dev)
parser = [bool(ev.venue_same_place(e.rules_pred, e.gold)) for e in exs]
for name, path in (("pretrained, NOT fine-tuned", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"),
                   ("fine-tuned (M2 v3)", str(HERE.parents[1] / "data" / "models" / "reranker_v3_clean_pick_acct_frac1.0"))):
    tok, model = AutoTokenizer.from_pretrained(path), AutoModelForSequenceClassification.from_pretrained(path).eval()
    preds, hits = M.evaluate(model, tok, exs, "cpu")
    b, a, p = ev.mcnemar_exact(parser, hits)
    tol = sum(M.tolerant(q, e.gold) for q, e in zip(preds, exs))
    nonone = sum(1 for q in preds if q is None)
    print(f"{name:28} dev {ev.format_rate(sum(hits), len(exs))} · tolerant {tol}/{len(exs)} · "
          f"says 'no venue' on {nonone} · vs parser {sum(parser)}/{len(exs)}: parser-only {b}, model-only {a}, p={p:.3g}")
