# SSL venue study (Nick)

Does self-supervised pretraining on our own captions help a model find venue names?

| Step | What | Labels? |
|---|---|---|
| 0 `export_text.py` | train-split captions/transcripts → `data/ssl/corpus.txt` | no |
| 1 `pretrain_mlm.py` | continue DistilBERT masked-LM on that text → `distilbert-food` | **no (SSL)** |
| 2 (next) | fine-tune plain vs `distilbert-food` to tag venue spans, train split | yes |
| 3 (next) | score both on the test split; Wilson CI + McNemar vs the rules (53.4% same-place) | yes |

Rules: test-split text is never used for pretraining or tuning; everything runs locally
(no paid APIs, captions stay on the machine); outputs live in `data/` (gitignored).

```powershell
.\.venv\Scripts\python.exe -m pip install -r experiments\ssl\requirements.txt
.\.venv\Scripts\python.exe experiments\ssl\export_text.py
.\.venv\Scripts\python.exe experiments\ssl\pretrain_mlm.py
```
Step 1 prints held-out perplexity before → after; a drop means it learned the domain.
