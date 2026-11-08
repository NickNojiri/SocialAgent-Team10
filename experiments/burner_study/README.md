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

## Using it in SpotBot

The trainer saves the model to `data/models/reranker_v5_tune_e5_frac<frac>/`. SpotBot loads it
when `.env` has `PLACE_PICKER_MODEL=<that folder>` (and `torch` + `transformers` are installed):
`src/ingestion/pipeline/place_picker.py` builds the same candidates and input text, and the
model's pick replaces the rules' venue when it differs. Measure the product path before turning
it on: `python -m src.ingestion.pipeline.place_picker --model <folder> --split dev`.

## Training M2 on your own Instagram export (`my_export.py`)

Adds the reels from your "Download your information" export to M2's training, and checks
the result on reels it never saw. Everything goes to `data/my_export/` (never committed).

```bash
python experiments/burner_study/my_export.py extract path/to/instagram-export.zip
python experiments/burner_study/my_export.py label             # train pool; resumes where you stopped
python experiments/burner_study/my_export.py label --heldout   # held-out pool; aim for 50+
python experiments/burner_study/my_export.py train             # base vs +your data, seeds 13/14/15 (overnight)
python experiments/burner_study/my_export.py report            # data/my_export/report.md
```

Reels from test-split creators are dropped and reels from dev-split creators are only ever
scored, so the frozen dev number stays comparable with M2 v5 and test stays unread.
