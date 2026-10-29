"""Does M2's lead over the parser hold on dev reels labeled by PEOPLE only?
Dev gold with labeler 'claude' was made by an earlier Claude session; scoring against it is
partly circular. This re-scores the saved M2 models and the parser on (a) all dev reels and
(b) dev reels whose labeler is not 'claude'."""
import sys
from pathlib import Path

from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev  # noqa: E402

MODELS = {"v1_raw": (dict(clean=False, parser_pick=False, accounts=True), "reranker_base_frac1.0"),
          "v3_clean_pick_acct": (dict(clean=True, parser_pick=True, accounts=True), "reranker_v3_clean_pick_acct_frac1.0")}
dev = [r for r in corpus if ev.split_of(r) == "dev" and M.V.scoreable(r)]
human = [r.get("labeler") != "claude" for r in dev]
print(f"dev reels {len(dev)} · labeled by people {sum(human)} · by an earlier Claude session {len(dev) - sum(human)}")
for name, (opts, folder) in MODELS.items():
    path = HERE.parents[1] / "data" / "models" / folder
    if not path.exists():
        print(f"  {name}: model not found ({folder})")
        continue
    M.OPTS.update(opts)
    exs = M.build(dev)
    tok, model = AutoTokenizer.from_pretrained(path), AutoModelForSequenceClassification.from_pretrained(path).eval()
    _, ml = M.evaluate(model, tok, exs, "cpu")
    parser = [bool(ev.venue_same_place(e.rules_pred, e.gold)) for e in exs]
    for label, keep in (("all dev", [True] * len(dev)), ("people-labeled only", human), ("Claude-labeled only", [not h for h in human])):
        p = [x for x, k in zip(parser, keep) if k]
        m = [x for x, k in zip(ml, keep) if k]
        b, a, pv = ev.mcnemar_exact(p, m)
        print(f"  {name:20} {label:20} parser {sum(p)}/{len(p)} ({100 * sum(p) / len(p):.1f}%) | "
              f"M2 {sum(m)}/{len(m)} ({100 * sum(m) / len(m):.1f}%) | parser-only {b}, M2-only {a}, p={pv:.3g}")
