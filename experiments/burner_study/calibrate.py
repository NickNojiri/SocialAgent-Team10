"""Test 6: calibration (temperature scaling) for M2, so its confidence can drive the
venue-card / summary-card cutoff.

1. Rebuild M2's validation set exactly as ml_reranker.py did for this seed (15% of TRAIN
   creators, gold only) and score it with the trained model.
2. Fit one temperature T on validation by minimizing the negative log-likelihood of the right
   option (softmax(scores / T)). T > 1 means the model was over-confident.
3. On dev (cached scores from compare_models.py): expected calibration error before/after, and
   selective prediction with a confidence cutoff chosen on VALIDATION for >= 90% precision,
   next to the agreement gate (model = parser).
Writes compare/calibration.md and <model>/temperature.json.
    python experiments/burner_study/calibrate.py [arm] [seed]     (default small_s13 13)
"""
import json
import random
import sys
from pathlib import Path

import numpy as np
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import compare_models as C  # noqa: E402
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev, gold_new, silver_new  # noqa: E402


def validation(seed):
    """The held-out train creators ml_reranker.py early-stopped on (same shuffle, same 15%)."""
    train = [r for r in corpus + gold_new + silver_new if ev.split_of(r) == "train" and M.V.scoreable(r)]
    creators = sorted({ev.creator_of(r) for r in train})
    random.Random(seed).shuffle(creators)
    val_c = set(creators[:max(1, int(0.15 * len(creators)))])
    return [e for e in M.build([r for r in train if ev.creator_of(r) in val_c]) if not e.silver]


def nll(scores, targets, t):
    return -np.mean([np.log(C.softmax(s, t)[y] + 1e-12) for s, y in zip(scores, targets)])


def ece(conf, hit, bins=10):
    conf, hit = np.array(conf), np.array(hit, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            total += m.mean() * abs(conf[m].mean() - hit[m].mean())
    return total


def main():
    arm = sys.argv[1] if len(sys.argv) > 1 else "small_s13"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 13
    folder, extra = C.FT[arm]
    d = M.DATA / "models" / folder
    dev_got = C.cache("ft_" + arm)[1]
    exs = C.dev_examples(extra)
    if not d.exists() or len(dev_got) < len(exs):
        raise SystemExit(f"{arm}: model or dev scores missing (run compare_models.py --stage ft first)")
    M.OPTS.update(clean=True, parser_pick=True, accounts=True, recipe_cue=True, **extra)
    val = validation(seed)
    tok, model = AutoTokenizer.from_pretrained(d), AutoModelForSequenceClassification.from_pretrained(d).eval()
    vs = [M.scores_for(model, tok, e, "cpu").float().numpy() for e in val]
    keep = [i for i, e in enumerate(val) if e.target >= 0]               # reels whose answer is a candidate
    grid = np.exp(np.linspace(np.log(0.25), np.log(8), 200))
    t = float(min(grid, key=lambda x: nll([vs[i] for i in keep], [val[i].target for i in keep], x)))
    (d / "temperature.json").write_text(json.dumps({"temperature": t, "fit_on": f"validation, seed {seed}, {len(keep)} reels"}),
                                        encoding="utf-8", newline="\n")

    def conf_hit(scores_list, examples, temp):
        conf, hit = [], []
        for s, e in zip(scores_list, examples):
            p = C.softmax(s, temp)
            j = int(np.argmax(p))
            pred = None if e.options[j] == M.NONE else e.options[j]
            conf.append(float(p[j]))
            hit.append(C.same(pred, e.gold))
        return conf, hit

    vconf, vhit = conf_hit(vs, val, t)
    cut = next((c for c in sorted(set(vconf)) if np.mean([h for x, h in zip(vconf, vhit) if x >= c]) >= 0.90), None)
    ds = [dev_got[C.code(e)]["scores"] for e in exs]
    lines = [f"# Calibration ({arm})", "",
             f"Temperature fitted on {len(keep)} validation reels (held-out train creators): **T = {t:.2f}** "
             f"({'over' if t > 1 else 'under'}-confident before).", "",
             "| dev | expected calibration error | mean confidence | accuracy |", "|---|---|---|---|"]
    for name, temp in (("before (T = 1)", 1.0), (f"after (T = {t:.2f})", t)):
        conf, hit = conf_hit(ds, exs, temp)
        lines.append(f"| {name} | {ece(conf, hit):.3f} | {np.mean(conf):.2f} | {np.mean(hit):.1%} |")
    conf, hit = conf_hit(ds, exs, t)
    lines += ["", "Selective prediction on dev (venue card only when confident; summary card otherwise):", "",
              "| rule | coverage | precision (strict) |", "|---|---|---|"]
    if cut is not None:
        m = [h for c, h in zip(conf, hit) if c >= cut]
        lines.append(f"| calibrated confidence >= {cut:.2f} (chosen on validation for 90%) | {len(m) / len(exs):.0%} | "
                     f"{sum(m)}/{len(m)} ({np.mean(m):.0%}) |")
    for c in (0.5, 0.6, 0.7, 0.8, 0.9):
        v = [h for x, h in zip(vconf, vhit) if x >= c]
        m = [h for x, h in zip(conf, hit) if x >= c]
        if m:
            lines.append(f"| calibrated confidence >= {c:.1f} (validation: {len(v) / len(val):.0%} at {np.mean(v) if v else 0:.0%}) | "
                         f"{len(m) / len(exs):.0%} | {sum(m)}/{len(m)} ({np.mean(m):.0%}) |")
    gate = [C.same(dev_got[C.code(e)]["pred"], e.gold) for e in exs if C.same(dev_got[C.code(e)]["pred"], e.rules_pred)]
    lines.append(f"| agreement gate (model = parser) | {len(gate) / len(exs):.0%} | {sum(gate)}/{len(gate)} ({np.mean(gate):.0%}) |")
    both = [h for c, h, e in zip(conf, hit, exs) if cut is not None and c >= cut and C.same(dev_got[C.code(e)]["pred"], e.rules_pred)]
    if both:
        lines.append(f"| both | {len(both) / len(exs):.0%} | {sum(both)}/{len(both)} ({np.mean(both):.0%}) |")
    text = "\n".join(lines)
    (C.OUT / "calibration.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
