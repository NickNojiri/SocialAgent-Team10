"""Fine-tune a small multilingual transformer to tag the venue span in a caption.

Same data and scoring as crf_baseline.py: train on train-split rows (corpus gold + burner
gold + burner silver; venue spans from distant supervision), score the SAME original dev
reels end to end with eval.py's "same place" test and the tolerant test. CPU only. The one
network use is the one-time model download; no post text leaves the machine.

    python experiments/burner_study/finetune_tagger.py [--model distilbert-base-multilingual-cased] [--epochs 4]
"""
import argparse
import os
import random
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
DATA = REPO / "data"
os.chdir(REPO)
sys.path.insert(0, str(REPO))
try:
    import truststore  # home network intercepts TLS (model download)
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

import torch  # noqa: E402
from transformers import AutoModelForTokenClassification, AutoTokenizer  # noqa: E402

from src.ingestion import eval as ev  # noqa: E402

exec((HERE / "alias_test.py").read_text(encoding="utf-8").split("arms = {")[0])  # corpus, gold_new, silver_new
LABELS = ["O", "B-VEN", "I-VEN"]
flat = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())


def venue_chars(text, venue):
    """Character span of the venue in the caption: the name, else an @handle spelling it."""
    m = re.search(re.escape(venue), text, re.I)
    if m:
        return m.start(), m.end()
    v = flat(venue)
    for h in re.finditer(r"@[\w.]+", text):
        f = flat(h.group())
        if len(v) >= 4 and (f == v or (v in f and len(f) - len(v) <= 6)):
            return h.start(), h.end()
    return None


def example(row):
    text = ((row.get("input") or {}).get("caption") or "")[:1500]
    if not text.strip():
        return None
    g = row.get("gold") or {}
    if g.get("venue"):
        span = venue_chars(text, g["venue"])
        return (text, span) if span else None
    return (text, None) if g.get("in_catalog") is False else None


def encode(tok, text, span, max_len):
    enc = tok(text, truncation=True, max_length=max_len, return_offsets_mapping=True)
    labels, inside = [], False
    for s, e in enc["offset_mapping"]:
        if s == e:
            labels.append(-100)
            continue
        if span and s >= span[0] and e <= span[1] + 1 and s < span[1]:
            labels.append(2 if inside else 1)
            inside = True
        else:
            labels.append(0)
            inside = False if not span or s >= span[1] else inside
    enc["labels"] = labels
    enc.pop("offset_mapping")
    return enc


def predict(model, tok, text, max_len):
    enc = tok(text[:1500], truncation=True, max_length=max_len, return_offsets_mapping=True, return_tensors="pt")
    offsets = enc.pop("offset_mapping")[0].tolist()
    with torch.no_grad():
        probs = model(**enc).logits[0].softmax(-1)
    # Join neighboring venue tokens (B or I: the model often puts B on every word piece), then
    # widen the span to whole words so 'Tien' + 'dita' reads 'Tiendita'. Best span = highest mean prob.
    spans, cur = [], None
    for i, (s, e) in enumerate(offsets):
        if s == e:
            continue
        p = probs[i, 1].item() + probs[i, 2].item()
        if p > 0.5 and (cur is None or s - cur[1] <= 1):
            cur = cur or [s, e, []]
            cur[1] = e
            cur[2].append(p)
        elif p > 0.5:
            spans.append(cur)
            cur = [s, e, [p]]
        else:
            if cur:
                spans.append(cur)
            cur = None
    if cur:
        spans.append(cur)
    if not spans:
        return None, 0.0
    s, e, ps = max(spans, key=lambda c: sum(c[2]) / len(c[2]))
    score = sum(ps) / len(ps)
    while s > 0 and not text[s - 1].isspace():
        s -= 1
    while e < len(text) and not text[e].isspace():
        e += 1
    best = (s, e)
    name = text[best[0]:best[1]].strip(" .,:-|!?📍")
    if name.startswith("@"):
        name = re.sub(r"[_.]+", " ", name[1:]).strip()
    return name or None, score


def tolerant(pred, gold):
    if ev.venue_same_place(pred, gold):
        return True
    a, b = flat((pred or "").replace("&", "and")), flat((gold or "").replace("&", "and"))
    return bool(a and b) and min(len(a), len(b)) >= 4 and (a in b or b in a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="distilbert-base-multilingual-cased")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=192)
    args = ap.parse_args()
    random.seed(0)
    torch.manual_seed(0)

    rows = corpus + gold_new + silver_new
    train = [r for r in rows if ev.split_of(r) == "train" and (r.get("verdict") or {}).get("venue") != "needs_review"]
    data = [x for x in (example(r) for r in train) if x]
    print(f"train examples {len(data)} (with venue span {sum(1 for _, s in data if s)})", flush=True)

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForTokenClassification.from_pretrained(
        args.model, num_labels=3, id2label=dict(enumerate(LABELS)), label2id={l: i for i, l in enumerate(LABELS)})
    enc = [encode(tok, t, s, args.max_len) for t, s in data]
    opt = torch.optim.AdamW(model.parameters(), lr=5e-5, weight_decay=0.01)
    weights = torch.tensor([1.0, 8.0, 8.0])        # venue tokens are rare: weight them up
    loss_fn = torch.nn.CrossEntropyLoss(weight=weights, ignore_index=-100)
    t0 = time.time()
    model.train()
    for epoch in range(args.epochs):
        random.shuffle(enc)
        total = 0.0
        for i in range(0, len(enc), 16):
            batch = tok.pad([{k: e[k] for k in ("input_ids", "attention_mask")} for e in enc[i:i + 16]], return_tensors="pt")
            width = batch["input_ids"].shape[1]
            labels = torch.tensor([e["labels"] + [-100] * (width - len(e["labels"])) for e in enc[i:i + 16]])
            logits = model(**batch).logits
            loss = loss_fn(logits.view(-1, 3), labels.view(-1))
            loss.backward()
            opt.step()
            opt.zero_grad()
            total += loss.item()
        print(f"epoch {epoch + 1}: loss {total / max(1, len(enc) // 16):.3f} ({time.time() - t0:.0f}s)", flush=True)
    model.eval()

    ev.use_aliases("fit")
    dev = [r for r in corpus if ev.split_of(r) == "dev"]
    from src.ingestion.config import IngestionSettings
    parser = {r["code"]: r for r in ev.score(dev, use_llm=False, settings=IngestionSettings()).rows}
    n = sp = sm = tp = tm = combo = combo_t = 0
    for row in dev:
        g, v = row.get("gold", {}), row.get("verdict", {})
        if not (v.get("venue") in ev._SCOREABLE_VERDICTS and (g.get("venue") or g.get("in_catalog") is False)):
            continue
        n += 1
        pv = parser[ev._code(row["url"])]["pred_venue"]
        mv, conf = predict(model, tok, (row.get("input") or {}).get("caption") or "", args.max_len)
        gv = g.get("venue")
        sp += ev.venue_same_place(pv, gv)
        sm += ev.venue_same_place(mv, gv)
        tp += tolerant(pv, gv)
        tm += tolerant(mv, gv)
        pick = pv or mv                                   # model fills only where the parser is empty
        combo += ev.venue_same_place(pick, gv)
        combo_t += tolerant(pick, gv)
    print(f"\ndev reels {n} ({args.model}, {args.epochs} epochs, {time.time() - t0:.0f}s on CPU)")
    print(f"  parser                       same place {ev.format_rate(sp, n)}   tolerant {tp}/{n}")
    print(f"  transformer                  same place {ev.format_rate(sm, n)}   tolerant {tm}/{n}")
    print(f"  parser, transformer if empty same place {ev.format_rate(combo, n)}   tolerant {combo_t}/{n}")
    out = DATA / "models" / "span_tagger_v0"
    model.save_pretrained(out)
    tok.save_pretrained(out)


if __name__ == "__main__":
    main()
