# Burner study: the M2 place picker

M2 is a small cross-encoder (mMiniLM-L12) that reads a reel's prepared text and picks the right place
from the candidates. Full results: `docs/results/2026-10-27-evaluation-report.html`.

## The recommended model: M2 v5, 5 epochs (74.2% on 271 dev reels)

```bash
python experiments/burner_study/ml_reranker.py --clean --parser-pick --recipe-cue --dev-plus --epochs 5 --seed 13 --tag v5_tune_e5
```

`--epochs 5` matters: the trainer defaults to 3, which is the older, weaker v5 run. The hand-written
parser SpotBot uses today gets 62.0% on the same 271 reels. `queue7.py` repeats this run with seeds 14
and 15 to confirm it before anyone reads the test split.

Training needs the prepared inputs under `data/`, which stay out of git.
