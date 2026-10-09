"""Step 1 (self-supervised): continue DistilBERT's masked-language-model training on
our own captions. No labels: 15% of tokens are hidden and the model learns to guess
them, which teaches it how food posts are written (📍 lines, @handles, dish names).

    python experiments/ssl/pretrain_mlm.py            # ~10-30 min on a laptop CPU
    -> data/ssl/distilbert-food/   (compare against plain distilbert in step 2)

Everything runs locally; captions never leave the machine (SRS C-2).
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

from datasets import load_dataset
from transformers import (
    AutoModelForMaskedLM, AutoTokenizer, DataCollatorForLanguageModeling,
    Trainer, TrainingArguments,
)

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="distilbert-base-uncased")
    ap.add_argument("--corpus", type=Path, default=REPO / "data" / "ssl" / "corpus.txt")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "ssl" / "distilbert-food")
    ap.add_argument("--epochs", type=float, default=5)
    ap.add_argument("--seed", type=int, default=491)
    args = ap.parse_args()

    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForMaskedLM.from_pretrained(args.base)

    ds = load_dataset("text", data_files=str(args.corpus))["train"].train_test_split(0.1, seed=args.seed)
    ds = ds.map(lambda b: tok(b["text"], truncation=True, max_length=256), batched=True, remove_columns=["text"])
    collator = DataCollatorForLanguageModeling(tok, mlm_probability=0.15)

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(args.out), num_train_epochs=args.epochs, per_device_train_batch_size=16,
            learning_rate=5e-5, eval_strategy="epoch", save_strategy="no", seed=args.seed,
            logging_steps=20, report_to=[],
        ),
        train_dataset=ds["train"], eval_dataset=ds["test"], data_collator=collator,
    )
    before = trainer.evaluate()["eval_loss"]
    trainer.train()
    after = trainer.evaluate()["eval_loss"]
    # Perplexity = how surprised the model is by unseen captions; lower = it learned the domain.
    print(f"held-out perplexity: {math.exp(before):.1f} -> {math.exp(after):.1f}")
    trainer.save_model(str(args.out))
    tok.save_pretrained(str(args.out))


if __name__ == "__main__":
    main()
