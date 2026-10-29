"""A fine-tuned transformer cross-encoder that picks the venue from a reel's candidates.

Learns from the text itself instead of hand-made features: every candidate name is paired
with the reel's whole context (poster handle + display name + account type, location tag,
caption, and speech / on-screen text when the video was transcribed) and a pretrained
multilingual cross-encoder scores the pair. The scores of a reel's candidates plus a
"[NO VENUE]" option go through a softmax, and the model is fine-tuned end to end with
cross-entropy on the right option (listwise ranking).

Data discipline (docs/DATA_PLAN.md):
  - trains on the train split only (corpus gold + Nick's confirmed burner reels + Claude's
    silver burner reels, silver down-weighted);
  - early stopping on a held-out 15% of train CREATORS (never dev);
  - dev is scored once per finished model, paired McNemar vs the parser; test is never read.

    python experiments/burner_study/ml_reranker.py [--frac 1.0] [--epochs 3] [--model NAME]
"""
import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass
import ranker_v3 as V  # noqa: E402
from burner_common import corpus, ev, gold_new, silver_new, squash  # noqa: E402

DATA = HERE.parents[1] / "data"
NONE = "[NO VENUE]"
MAX_CANDS = 12
KIND = {1: "personal account", 2: "business account", 3: "creator account"}


def load_jsonl(path, key):
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                d = json.loads(line)
                out[d[key].lower() if key == "username" else d[key]] = d
    return out


AV = {**load_jsonl(DATA / "experiments" / "pilot_av.jsonl", "code"), **load_jsonl(DATA / "experiments" / "av_run.jsonl", "code"),
      **load_jsonl(DATA / "experiments" / "dev_av.jsonl", "code")}
ACCOUNTS = load_jsonl(DATA / "experiments" / "accounts.jsonl", "username")
# The local LLM's own free-text answer per reel (llm_hints.py), for the --llm-hint ensemble.
LLM_HINTS = {c: d.get("venue") for c, d in load_jsonl(DATA / "experiments" / "llm_hints.jsonl", "code").items()}
# Input variants (ablations), set from the command line:
#   clean        the prepared view from clean_inputs.enrich_row (cleaned text, full captions,
#                display names, city-only tags as hints, cleaned speech / screen text)
#   parser_pick  tell the model which candidate the rule parser chose
#   accounts     include the poster's Instagram account type + category
#   llm_hint     show the local LLM's answer in the input, and add it as a candidate if missing
OPTS = {"clean": False, "parser_pick": False, "accounts": True, "recipe_cue": False, "llm_hint": False}
RECIPE_WORDS = re.compile(r"\b(recipe|ingredients?|tbsp|tsp|tablespoons?|teaspoons?|preheat|bake at|\d+\s?(?:g|grams|cups?|oz)\b)", re.I)


def looks_like_recipe(caption):
    """The parser's own recipe detectors, or at least two cooking/measurement words."""
    from burner_common import nz
    return bool(nz._RECIPE_RE.search(caption) or nz._RECIPE_CTA.search(caption) or len(RECIPE_WORDS.findall(caption)) >= 2)


def context(row, parser_pick=None):
    inp = row.get("input") or {}
    h = (inp.get("handle") or "").lstrip("@")
    a = ACCOUNTS.get(h.lower()) if OPTS["accounts"] else None
    acct = ""
    if a:
        cat = a.get("category_name") or a.get("business_category_name") or a.get("category") or ""
        acct = (KIND.get(a.get("account_type")) or "") + (f", {cat}" if cat else "")
    parts = [f"poster: @{h}" + (f" ({inp.get('author_name')})" if inp.get("author_name") else "") + (f" [{acct}]" if acct else "")]
    if OPTS["parser_pick"]:
        parts.append(f"parser pick: {parser_pick or 'none'}")
    if OPTS["llm_hint"]:
        parts.append(f"local LLM says: {LLM_HINTS.get(ev._code(row['url'])) or 'none'}")
    if OPTS["recipe_cue"] and looks_like_recipe(inp.get("caption") or ""):
        parts.append("post looks like: recipe")
    if inp.get("location_text"):
        parts.append(f"location tag: {inp['location_text']}")
    elif inp.get("location_city"):
        parts.append(f"city tag: {inp['location_city']}")
    parts.append("caption: " + " ".join((inp.get("caption") or "").split())[:700])
    x = AV.get(ev._code(row["url"])) or {}
    speech = inp.get("transcript") if OPTS["clean"] else x.get("transcript")
    screen = inp.get("ocr") if OPTS["clean"] else x.get("ocr")
    if speech:
        parts.append("said in video: " + " ".join(speech.split())[:400])
    if screen:
        parts.append("on screen: " + screen[:200])
    return " | ".join(parts)


class Example:
    def __init__(self, row, reel):
        self.row, self.gold, self.rules_pred = row, reel.gold, reel.rules_pred
        self.silver = row.get("source") == "silver"
        names = [c.name for c in reel.cands]
        t = reel.target
        if len(names) > MAX_CANDS:                       # keep the rules' pick, the target, then the rest in order
            keep = [i for i, c in enumerate(reel.cands) if c.rules_slot] + ([t] if 0 <= t < len(names) else [])
            keep += [i for i in range(len(names)) if i not in keep]
            keep = list(dict.fromkeys(keep))[:MAX_CANDS]
            t = keep.index(t) if t in keep else (-1 if t < len(names) else t)
            names = [names[i] for i in keep]
        n0 = len(names)
        hint = LLM_HINTS.get(ev._code(row["url"])) if OPTS["llm_hint"] else None
        if hint and not any(ev.venue_same_place(hint, n) for n in names):   # the LLM found a name the extractor missed
            names = names + [hint]
        self.options = names + [NONE]
        self.target = len(names) if not self.gold else (t if t < n0 else -1)
        if self.gold and self.target < 0 and len(names) > n0 and ev.venue_same_place(hint, self.gold):
            self.target = n0
        self.ctx = context(row, reel.rules_pred)

    def pick(self, scores):
        j = int(np.argmax(scores))
        return None if self.options[j] == NONE else self.options[j]


def build(rows):
    if OPTS["clean"]:
        import clean_inputs as CI
        rows = [CI.enrich_row(r, use_accounts=OPTS["accounts"]) for r in rows]
    return [Example(r, V.Reel(r, 0.5)) for r in rows]


def scores_for(model, tok, ex, device, grad=False):
    enc = tok(ex.options, [ex.ctx] * len(ex.options), truncation="only_second", max_length=256,
              padding=True, return_tensors="pt").to(device)
    with torch.set_grad_enabled(grad):
        return model(**enc).logits.squeeze(-1)


def evaluate(model, tok, exs, device):
    model.eval()
    preds = [ex.pick(scores_for(model, tok, ex, device).float().cpu().numpy()) for ex in exs]
    return preds, [bool(ev.venue_same_place(p, ex.gold)) for p, ex in zip(preds, exs)]


def tolerant(pred, gold):
    if ev.venue_same_place(pred, gold):
        return True
    a, b = squash((pred or "").replace("&", "and")), squash((gold or "").replace("&", "and"))
    return bool(a and b) and min(len(a), len(b)) >= 4 and (a in b or b in a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
    ap.add_argument("--frac", type=float, default=1.0, help="fraction of training creators to use (learning curve)")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--silver-weight", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--clean", action="store_true", help="read the prepared (cleaned + enriched) inputs")
    ap.add_argument("--parser-pick", action="store_true", help="tell the model which candidate the parser chose")
    ap.add_argument("--no-accounts", action="store_true", help="leave out the poster's account type")
    ap.add_argument("--recipe-cue", action="store_true", help="flag posts that look like recipes in the input")
    ap.add_argument("--dev-plus", action="store_true", help="also score Nick's confirmed burner reels that fall in dev")
    ap.add_argument("--llm-hint", action="store_true", help="give the model the local LLM's answer (llm_hints.py)")
    ap.add_argument("--freeze-embeddings", action="store_true",
                    help="keep the pretrained word-embedding table fixed (most of a multilingual model's weights)")
    ap.add_argument("--tag", default="", help="name for this variant in the results file")
    args = ap.parse_args()
    OPTS.update(clean=args.clean, parser_pick=args.parser_pick, accounts=not args.no_accounts, recipe_cue=args.recipe_cue,
                llm_hint=args.llm_hint)
    if args.llm_hint and not LLM_HINTS:
        raise SystemExit("--llm-hint needs data/experiments/llm_hints.jsonl (run llm_hints.py first)")
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(max(1, (torch.get_num_threads() or 4)))
    device = "cpu"

    train_rows = [r for r in corpus + gold_new + silver_new if ev.split_of(r) == "train" and V.scoreable(r)]
    dev_rows = [r for r in corpus if ev.split_of(r) == "dev" and V.scoreable(r)]
    if args.dev_plus:
        dev_rows += [r for r in gold_new if ev.split_of(r) == "dev" and V.scoreable(r)]
    creators = sorted({ev.creator_of(r) for r in train_rows})
    random.Random(args.seed).shuffle(creators)
    n_val = max(1, int(0.15 * len(creators)))
    val_c = set(creators[:n_val])
    fit_c = creators[n_val:]
    fit_c = set(fit_c[:max(1, int(args.frac * len(fit_c)))])
    t0 = time.time()
    fit = [e for e in build([r for r in train_rows if ev.creator_of(r) in fit_c]) if e.target >= 0]
    val = [e for e in build([r for r in train_rows if ev.creator_of(r) in val_c]) if not e.silver]
    dev = build(dev_rows)
    print(f"examples: fit {len(fit)} (silver {sum(e.silver for e in fit)}) | val {len(val)} gold | dev {len(dev)}"
          f" | built in {time.time() - t0:.0f}s", flush=True)

    tok = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(args.model, num_labels=1).to(device)
    if args.freeze_embeddings:
        for p in model.base_model.embeddings.parameters():
            p.requires_grad_(False)
    trainable = [p for p in model.parameters() if p.requires_grad]
    print(f"trainable parameters: {sum(p.numel() for p in trainable) / 1e6:.0f}M of "
          f"{sum(p.numel() for p in model.parameters()) / 1e6:.0f}M", flush=True)
    opt = torch.optim.AdamW(trainable, lr=args.lr, weight_decay=0.01)
    steps = args.epochs * len(fit) // 8
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, steps // 10)) * max(0.0, 1 - s / max(1, steps)))
    ce = torch.nn.CrossEntropyLoss()
    best, best_state = -1.0, None
    for epoch in range(args.epochs):
        model.train()
        random.shuffle(fit)
        total, t0 = 0.0, time.time()
        for i, ex in enumerate(fit, 1):
            s = scores_for(model, tok, ex, device, grad=True)
            w = args.silver_weight if ex.silver else 1.0
            loss = w * ce(s.unsqueeze(0), torch.tensor([ex.target], device=device)) / 8
            loss.backward()
            total += loss.item() * 8
            if i % 8 == 0 or i == len(fit):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                sched.step()
                opt.zero_grad()
            if i % 50 == 0:
                print(f"  epoch {epoch + 1}: {i}/{len(fit)} reels, loss {total / i:.3f} ({time.time() - t0:.0f}s)", flush=True)
        _, vh = evaluate(model, tok, val, device)
        acc = sum(vh) / max(1, len(vh))
        print(f"epoch {epoch + 1}: train loss {total / len(fit):.3f} | held-out train creators {acc:.3f} "
              f"({time.time() - t0:.0f}s)", flush=True)
        if acc > best:
            best = acc
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)

    preds, hits = evaluate(model, tok, dev, device)
    rules = [bool(ev.venue_same_place(e.rules_pred, e.gold)) for e in dev]
    b_only, a_only, p = ev.mcnemar_exact(rules, hits)
    ci = ev.paired_delta_interval(rules, hits)
    print(f"\nDEV ({len(dev)} reels) · frac {args.frac} · best held-out {best:.3f}")
    print(f"   parser       : {ev.format_rate(sum(rules), len(dev))}")
    print(f"   ML re-ranker : {ev.format_rate(sum(hits), len(dev))}")
    print(f"   change       : {(sum(hits) - sum(rules)) / len(dev):+.1%}" + (f" CI [{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "")
          + f" · parser only {b_only}, ML only {a_only}, McNemar p={p:.3g}")
    print(f"   tolerant     : parser {sum(tolerant(e.rules_pred, e.gold) for e in dev)} -> ML {sum(tolerant(q, e.gold) for q, e in zip(preds, dev))}")
    corpus_codes = {ev._code(r["url"]) for r in corpus}
    groups = {"original corpus dev": lambda e: ev._code(e.row["url"]) in corpus_codes,
              "Nick's confirmed burner reels": lambda e: ev._code(e.row["url"]) not in corpus_codes,
              "labeled by people (not an earlier Claude session)": lambda e: e.row.get("labeler") != "claude"}
    for name, keep in groups.items():
        idx = [i for i, e in enumerate(dev) if keep(e)]
        if not idx:
            continue
        rp, mp = [rules[i] for i in idx], [hits[i] for i in idx]
        b, a, pv = ev.mcnemar_exact(rp, mp)
        print(f"   {name:52} parser {sum(rp)}/{len(idx)} ({100 * sum(rp) / len(idx):.1f}%) | "
              f"ML {sum(mp)}/{len(idx)} ({100 * sum(mp) / len(idx):.1f}%) | p={pv:.3g}")
    out = DATA / "models" / f"reranker_{args.tag or 'base'}_frac{args.frac}"
    model.save_pretrained(out)
    tok.save_pretrained(out)
    with (DATA / "experiments" / "ml_reranker_results.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"tag": args.tag, "opts": OPTS, "model": args.model, "lr": args.lr, "freeze_embeddings": args.freeze_embeddings,
                            "frac": args.frac, "epochs": args.epochs, "fit": len(fit), "val_best": best,
                            "dev_parser": sum(rules), "dev_ml": sum(hits), "n_dev": len(dev), "p": p,
                            "dev_tolerant_ml": sum(tolerant(q, e.gold) for q, e in zip(preds, dev))}) + "\n")


if __name__ == "__main__":
    main()
