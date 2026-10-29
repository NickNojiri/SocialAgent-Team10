"""B2, the RNN baseline: a BiLSTM token tagger trained from scratch (no pretraining).

Task: token classification (per-token multiclass, 3 classes: O / B-VEN / I-VEN, the BIO scheme).
Input: the reel's text as a token sequence: caption + location tag + speech + on-screen text.
Each token = a learned word embedding (100-d, vocabulary built from the fit set) + 6 shape
features (Title-case, ALL CAPS, digit, @handle, #hashtag, 📍).
Model: embedding -> 1-layer bidirectional LSTM (128 per direction) -> dropout -> linear -> 3 logits.
Prediction: the contiguous B/I span with the highest mean probability, an @handle spelled as
a name; no span -> "no venue".
Loss: token-level cross-entropy, venue tokens up-weighted (rare class), padding ignored.
Optimizer: Adam, lr 2e-3, batch 32, up to 20 epochs, early stopping on held-out train creators.
Training labels: the same distant supervision as the CRF / transformer tagger (the labeled
venue string or its @handle located in the text). Dev is scored once, at the end.
"""
import random
import re
import sys
import time
from collections import Counter
from pathlib import Path

import torch
from torch import nn

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ml_reranker as M  # noqa: E402  (context data: speech/screen/accounts)
import ranker_v3 as V  # noqa: E402
from burner_common import corpus, ev, gold_new, nz, silver_new, squash  # noqa: E402

TOK = re.compile(r"@[\w.]+|#\w+|[\w'’&.-]+|📍|\S", re.U)
SEED = 13
random.seed(SEED)
torch.manual_seed(SEED)


def text_of(row):
    inp = row.get("input") or {}
    parts = [inp.get("caption") or "", inp.get("location_text") or ""]
    x = M.AV.get(ev._code(row["url"]))
    if x:
        parts += [x.get("transcript") or "", x.get("ocr") or ""]
    return " \n ".join(p for p in parts if p)[:2500]


def tokens(text):
    return [(m.group(), m.start(), m.end()) for m in TOK.finditer(text)][:300]


def span_labels(text, toks, venue):
    if not venue:
        return [0] * len(toks)
    m = re.search(re.escape(venue), text, re.I)
    if m:
        s, e = m.start(), m.end()
    else:
        v = squash(venue)
        hit = next((h for h in re.finditer(r"@[\w.]+", text)
                    if len(v) >= 4 and (squash(h.group()) == v or (v in squash(h.group()) and len(squash(h.group())) - len(v) <= 6))), None)
        if not hit:
            return None
        s, e = hit.start(), hit.end()
    labels, first = [], True
    for _, a, b in toks:
        if a < e and b > s:
            labels.append(1 if first else 2)
            first = False
        else:
            labels.append(0)
    return labels


def shape(t):
    return [float(t[:1].isupper()), float(t.isupper() and len(t) > 1), float(any(ch.isdigit() for ch in t)),
            float(t.startswith("@")), float(t.startswith("#")), float(t == "📍")]


class Tagger(nn.Module):
    def __init__(self, vocab, emb=100, hidden=128):
        super().__init__()
        self.emb = nn.Embedding(vocab, emb, padding_idx=0)
        self.lstm = nn.LSTM(emb + 6, hidden, batch_first=True, bidirectional=True)
        self.drop = nn.Dropout(0.3)
        self.out = nn.Linear(2 * hidden, 3)

    def forward(self, ids, feats):
        h, _ = self.lstm(torch.cat([self.drop(self.emb(ids)), feats], -1))
        return self.out(self.drop(h))


def encode(toks, vocab):
    return [vocab.get(t.lower(), 1) for t, _, _ in toks], [shape(t) for t, _, _ in toks]


def batches(items, size):
    for i in range(0, len(items), size):
        chunk = items[i:i + size]
        n = max(len(x[0]) for x in chunk)
        ids = torch.zeros(len(chunk), n, dtype=torch.long)
        feats = torch.zeros(len(chunk), n, 6)
        labels = torch.full((len(chunk), n), -100, dtype=torch.long)
        for j, (a, f, y) in enumerate(chunk):
            ids[j, :len(a)] = torch.tensor(a)
            feats[j, :len(f)] = torch.tensor(f)
            labels[j, :len(y)] = torch.tensor(y)
        yield ids, feats, labels


def predict(model, vocab, row):
    text = text_of(row)
    toks = tokens(text)
    if not toks:
        return None
    a, f = encode(toks, vocab)
    with torch.no_grad():
        p = model(torch.tensor([a]), torch.tensor([f])).softmax(-1)[0]
    spans, cur = [], None
    for i in range(len(toks)):
        q = float(p[i, 1] + p[i, 2])
        if q > 0.5:
            cur = cur or [i, i, []]
            cur[1] = i
            cur[2].append(q)
        elif cur:
            spans.append(cur)
            cur = None
    if cur:
        spans.append(cur)
    if not spans:
        return None
    s, e, _ = max(spans, key=lambda c: sum(c[2]) / len(c[2]))
    name = text[toks[s][1]:toks[e][2]].strip(" .,:-|!?📍")
    return re.sub(r"[_.]+", " ", name[1:]).strip() if name.startswith("@") else (name or None)


def accuracy(model, vocab, rows):
    model.eval()
    return [bool(ev.venue_same_place(predict(model, vocab, r), (r.get("gold") or {}).get("venue"))) for r in rows]


def main():
    train_rows = [r for r in corpus + gold_new + silver_new if ev.split_of(r) == "train" and V.scoreable(r)]
    dev_rows = [r for r in corpus if ev.split_of(r) == "dev" and V.scoreable(r)]
    creators = sorted({ev.creator_of(r) for r in train_rows})
    random.Random(SEED).shuffle(creators)
    val_c = set(creators[:max(1, int(0.15 * len(creators)))])
    fit_rows = [r for r in train_rows if ev.creator_of(r) not in val_c]
    val_rows = [r for r in train_rows if ev.creator_of(r) in val_c and r.get("source") != "silver"]

    data = []
    for r in fit_rows:
        text = text_of(r)
        toks = tokens(text)
        y = span_labels(text, toks, (r.get("gold") or {}).get("venue"))
        if toks and y is not None and ((r.get("gold") or {}).get("venue") or (r.get("gold") or {}).get("in_catalog") is False):
            data.append((toks, y))
    counts = Counter(t.lower() for toks, _ in data for t, _, _ in toks)
    vocab = {"<pad>": 0, "<unk>": 1}
    for w, c in counts.items():
        if c >= 2:
            vocab[w] = len(vocab)
    items = [(*encode(toks, vocab), y) for toks, y in data]
    print(f"B2 BiLSTM · fit examples {len(items)} (with a venue span {sum(1 in y for *_, y in items)}) · vocab {len(vocab)}"
          f" · val {len(val_rows)} gold · dev {len(dev_rows)}", flush=True)

    model = Tagger(len(vocab))
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor([1.0, 5.0, 5.0]), ignore_index=-100)
    best, best_state, patience = -1.0, None, 0
    for epoch in range(20):
        model.train()
        random.shuffle(items)
        total, t0 = 0.0, time.time()
        for ids, feats, labels in batches(items, 32):
            logits = model(ids, feats)
            loss = loss_fn(logits.reshape(-1, 3), labels.reshape(-1))
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            opt.step()
            total += loss.item()
        val = sum(accuracy(model, vocab, val_rows)) / max(1, len(val_rows))
        print(f"  epoch {epoch + 1:2}: loss {total:.2f} | held-out train creators {val:.3f} ({time.time() - t0:.0f}s)", flush=True)
        if val > best:
            best, best_state, patience = val, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            patience += 1
            if patience >= 4:
                break
    model.load_state_dict(best_state)
    hits = accuracy(model, vocab, dev_rows)
    out0 = {ev._code(r["url"]): nz.normalize(ev.raw_from_input(r["url"], r.get("input") or {})).get("venue_name") for r in dev_rows}
    rules = [bool(ev.venue_same_place(out0[ev._code(r["url"])], (r.get("gold") or {}).get("venue"))) for r in dev_rows]
    b, a, p = ev.mcnemar_exact(rules, hits)
    print(f"\nB2 BiLSTM on dev: {ev.format_rate(sum(hits), len(dev_rows))} (B1 parser {sum(rules)}/{len(dev_rows)}) · "
          f"parser only {b}, BiLSTM only {a}, McNemar p={p:.3g}")


if __name__ == "__main__":
    main()
