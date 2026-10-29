"""Collect the overnight results into one page: data/experiments/OVERNIGHT_RESULTS.md."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
DATA = HERE.parents[1] / "data"
OUT = DATA / "experiments" / "compare"
RESULTS = DATA / "experiments" / "ml_reranker_results.jsonl"
TAGS = ["v5_moregold", "v5_small_s14", "v5_small_s15", "v5_medium_s13", "v5_medium_s14", "v6_llmhint_s13",
        "v6_llmhint_s14", "v5_tune_e5", "v5_tune_lr3e5", "v5_tune_lr1e5_e5"]


def main():
    runs = {}
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                d = json.loads(line)
                runs[d.get("tag")] = d                     # the latest run of each tag
    parts = ["# Overnight results", "",
             "Every number is on dev (the 271 scoreable dev reels: original corpus + Nick's confirmed burner reels). "
             "Test was not read. Tuning choices use validation (held-out train creators), never dev.", "",
             "## Training runs", "", "| run | fit reels | validation (held-out train creators) | dev parser | dev model | tolerant | p vs parser |",
             "|---|---|---|---|---|---|---|"]
    for t in TAGS:
        d = runs.get(t)
        if d:
            parts.append(f"| {t} | {d['fit']} | {d['val_best']:.3f} | {d['dev_parser']}/{d['n_dev']} | "
                         f"**{d['dev_ml']}/{d['n_dev']}** ({d['dev_ml'] / d['n_dev']:.1%}) | {d.get('dev_tolerant_ml', '')} | {d['p']:.3g} |")
        else:
            parts.append(f"| {t} | not run | | | | | |")
    tune = [t for t in ("v5_moregold", "v5_tune_e5", "v5_tune_lr3e5", "v5_tune_lr1e5_e5") if t in runs]
    if len(tune) > 1:
        best = max(tune, key=lambda t: runs[t]["val_best"])
        parts += ["", f"Tuning pick by VALIDATION: **{best}** (validation {runs[best]['val_best']:.3f}). "
                      f"Its dev score is {runs[best]['dev_ml']}/{runs[best]['n_dev']}; the dev numbers of the others are shown "
                      "but were not used to choose."]
    for name in ("report.md", "error_analysis.md", "name_cleanup.md", "calibration.md", "post_type.md"):
        p = OUT / name
        parts += ["", "---", ""]
        if p.exists():
            body = p.read_text(encoding="utf-8")
            parts.append(body if body.lstrip().startswith("#") else "## Model comparison (big vs small vs medium vs both)\n\n" + body)
        else:
            parts.append(f"## {name}: not produced (see data/experiments/queue/*.log)")
    text = "\n".join(parts) + "\n"
    (DATA / "experiments" / "OVERNIGHT_RESULTS.md").write_text(text, encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
