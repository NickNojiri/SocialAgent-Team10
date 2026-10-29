"""Big vs small vs medium: does a large zero-shot LLM beat our small fine-tuned re-rankers, and
does combining them beat both?

Every arm sees the same dev reels, the same inputs (M2 v5's prepared context) and the same
candidates:
  FT small   mMiniLM-L12 cross-encoder (118M params) fine-tuned = M2 v5, seeds 13 and 14
  FT medium  BAAI/bge-reranker-base (XLM-R base reranker, 278M) fine-tuned with the same recipe,
             seeds 13 and 14 (mDeBERTa-v3-base was dropped: over 100 s per training step on this CPU)
  pretrained both checkpoints with no fine-tuning (the ablation)
  LLM        qwen2.5:7b and llama3.1:8b, zero-shot, multiple choice over the candidates + none
             (may also name a place that is not listed), prompted with the labeling rubric
  BOTH       the fine-tuned answer when it agrees with the parser (the agreement gate),
             otherwise the LLM chooses from the fine-tuned model's top 3 + none

Every answer is cached in data/experiments/compare/ so a crash or reboot resumes where it
stopped. Ollama runs locally (on the GPU when it found one); nothing leaves the machine.
Dev only; test is not read.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev, gold_new  # noqa: E402

OUT = M.DATA / "experiments" / "compare"
# name: (model folder, input options beyond v5's). Missing folders are skipped.
FT = {"small_s13": ("reranker_v5_moregold_frac1.0", {}), "small_s14": ("reranker_v5_small_s14_frac1.0", {}),
      "small_s15": ("reranker_v5_small_s15_frac1.0", {}),
      "medium_s13": ("reranker_v5_medium_s13_frac1.0", {}), "medium_s14": ("reranker_v5_medium_s14_frac1.0", {}),
      # the same two checkpoints BEFORE fine-tuning (what fine-tuning adds at each size)
      "small_pretrained": ("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", {}),
      "medium_pretrained": ("BAAI/bge-reranker-base", {}),
      "llmhint_s13": ("reranker_v6_llmhint_s13_frac1.0", {"llm_hint": True}),
      "llmhint_s14": ("reranker_v6_llmhint_s14_frac1.0", {"llm_hint": True})}
ENSEMBLES = {"ens_small": ["small_s13", "small_s14", "small_s15"], "ens_medium": ["medium_s13", "medium_s14"],
             "ens_llmhint": ["llmhint_s13", "llmhint_s14"]}
BOTH_FT = ["small_s13", "medium_s13"]          # the LLM-judge combination is tried on one seed of each size
LLMS = ["qwen2.5:7b", "llama3.1:8b"]
_EXS = {}
OLLAMA = "http://localhost:11434"

RUBRIC = """You decide which single real-world place (restaurant, cafe, bar, bakery, shop, venue) an
Instagram reel is about. Use these rules, in order:
1. If the poster is a business account posting about itself, the poster is the place.
2. A location tag that names a business (not just a city) is strong evidence.
3. A name said in the video or shown on screen (a sign, menu, logo) counts as much as the caption.
4. A partner/collab post between a creator and a business is about the business.
5. Food bloggers, reviewers and people's names are not places.
6. Recipes, memes, games, product ads, music, and lists of several places have no single place.
7. A name that is slightly off is fine: pick the closest option."""
ANSWER_MC = """Answer ONLY with JSON {"choice": <option number>, "other": null}. If the right place is
clearly named in the reel but missing from the options, answer {"choice": 0, "other": "<its name>"}."""
ANSWER_JUDGE = """Two systems disagree on this reel. Answer ONLY with JSON {"choice": <option number>}."""


def code(ex):
    return ev._code(ex.row["url"])


def same(a, b):
    return (not a and not b) or bool(a and b and ev.venue_same_place(a, b))


def dev_examples(extra=None):
    """Dev reels prepared with v5's inputs (plus any extra options), built once per option set."""
    key = tuple(sorted((extra or {}).items()))
    if key not in _EXS:
        M.OPTS.update({**dict(clean=True, parser_pick=True, accounts=True, recipe_cue=True, llm_hint=False), **(extra or {})})
        rows = [r for r in corpus + gold_new if ev.split_of(r) == "dev" and M.V.scoreable(r)]
        _EXS[key] = M.build(rows)
        M.OPTS.update(llm_hint=False)
    return _EXS[key]


def cache(name):
    path, done = OUT / f"{name}.jsonl", {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                o = json.loads(line)
                done[o["code"]] = o
    return path, done


def append(path, obj):
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def run_ft(name):
    folder, extra = FT[name]
    d = folder if "/" in folder else M.DATA / "models" / folder           # a Hugging Face id = an untouched checkpoint
    if not isinstance(d, str) and not d.exists():
        print(f"ft {name}: no model yet", flush=True)
        return
    exs = dev_examples(extra)
    path, done = cache("ft_" + name)
    todo = [e for e in exs if code(e) not in done]
    if not todo:
        print(f"ft {name}: cached", flush=True)
        return
    M.OPTS.update(llm_hint=bool(extra.get("llm_hint")))
    tok, model = AutoTokenizer.from_pretrained(d), AutoModelForSequenceClassification.from_pretrained(d, num_labels=1).eval()
    for ex in todo:
        t = time.time()
        s = M.scores_for(model, tok, ex, "cpu").float().numpy()
        order = np.argsort(-s)
        top = [ex.options[j] for j in order[:3]]
        append(path, {"code": code(ex), "pred": None if top[0] == M.NONE else top[0], "top3": top,
                      "margin": float(s[order[0]] - s[order[1]]) if len(s) > 1 else 99.0, "sec": time.time() - t,
                      "options": ex.options, "scores": [round(float(x), 4) for x in s]})
    M.OPTS.update(llm_hint=False)
    print(f"ft {name}: scored {len(todo)}", flush=True)


def softmax(scores, temperature=1.0):
    z = np.array(scores, dtype=float) / temperature
    e = np.exp(z - z.max())
    return e / e.sum()


def run_ensemble(name, members):
    """Average the members' softmax over the same options (seed ensemble)."""
    got = [cache("ft_" + m)[1] for m in members]
    if any(len(g) < len(dev_examples()) for g in got):
        print(f"{name}: members not all scored yet", flush=True)
        return
    path = OUT / f"{name}.jsonl"
    path.unlink(missing_ok=True)
    for c in got[0]:
        if any(g[c]["options"] != got[0][c]["options"] for g in got):
            raise SystemExit(f"{name}: members saw different options for {c}")
        probs = np.mean([softmax(g[c]["scores"]) for g in got], axis=0)
        best = got[0][c]["options"][int(np.argmax(probs))]
        append(path, {"code": c, "pred": None if best == M.NONE else best, "conf": float(probs.max()),
                      "sec": sum(g[c]["sec"] for g in got)})
    print(f"{name}: ensembled {len(members)} models", flush=True)


def ask(llm, prompt):
    body = json.dumps({"model": llm, "prompt": prompt, "format": "json", "stream": False, "keep_alive": "2m",
                       "options": {"temperature": 0, "num_ctx": 4096}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(json.loads(resp.read())["response"])


def gpu_share(llm):
    """How much of the loaded model Ollama put in GPU memory (1.0 = all on the GPU)."""
    with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=30) as resp:
        for m in json.loads(resp.read()).get("models", []):
            if m.get("name", "").startswith(llm):
                return m.get("size_vram", 0) / max(1, m.get("size", 1))
    return None


def menu(options):
    return "\n".join(f"{i}. {'none of these / not one specific place' if o == M.NONE else o}" for i, o in enumerate(options, 1))


def parse_choice(out, options, allow_other):
    other = out.get("other")
    if allow_other and isinstance(other, str) and other.strip() and other.strip().lower() not in ("null", "none"):
        return other.strip()
    try:
        j = int(out.get("choice"))
    except (TypeError, ValueError):
        return None
    return (None if options[j - 1] == M.NONE else options[j - 1]) if 1 <= j <= len(options) else None


def ask_cached(path, ex, llm, prompt, options, allow_other, extra, errors):
    t = time.time()
    try:
        pred = parse_choice(ask(llm, prompt), options, allow_other)
    except Exception as exc:  # noqa: BLE001 - not cached, so a resume retries it
        print(f"  {code(ex)}: ollama error {type(exc).__name__}", flush=True)
        errors[0] += 1
        if errors[0] >= 5:
            raise SystemExit("5 Ollama errors in a row - stopping (is the server up?)")
        return
    errors[0] = 0
    append(path, {"code": code(ex), "pred": pred, "sec": time.time() - t, **extra})


def run_llm(llm, exs):
    path, done = cache("llm_" + llm.replace(":", "_"))
    todo = [e for e in exs if code(e) not in done]
    errors, t0 = [0], time.time()
    for i, ex in enumerate(todo, 1):
        prompt = f"{RUBRIC}\n\nReel: {ex.ctx}\n\nOptions:\n{menu(ex.options)}\n\n{ANSWER_MC}"
        ask_cached(path, ex, llm, prompt, ex.options, True, {}, errors)
        if i == 1:
            share = gpu_share(llm)
            print(f"llm {llm}: {'unknown' if share is None else f'{share:.0%}'} of the model on the GPU", flush=True)
        if i % 25 == 0:
            print(f"  llm {llm}: {i}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"llm {llm}: {'scored ' + str(len(todo)) if todo else 'cached'}", flush=True)


def run_both(ft_name, llm, exs):
    ft = cache("ft_" + ft_name)[1]
    if len(ft) < len(exs):
        return
    path, done = cache(f"both_{ft_name}_{llm.replace(':', '_')}")
    errors = [0]
    for ex in exs:
        c = code(ex)
        if c in done:
            continue
        f = ft[c]
        if same(f["pred"], ex.rules_pred):              # gate passes: keep the fine-tuned answer
            append(path, {"code": c, "pred": f["pred"], "sec": f["sec"], "asked": False})
            continue
        opts = [o for o in f["top3"] if o != M.NONE] + [M.NONE]
        prompt = f"{RUBRIC}\n\nReel: {ex.ctx}\n\nOptions:\n{menu(opts)}\n\n{ANSWER_JUDGE}"
        ask_cached(path, ex, llm, prompt, opts, False, {"asked": True}, errors)
    print(f"both {ft_name} + {llm}: done", flush=True)


def report(exs):
    gold = {code(e): e.gold for e in exs}
    parser = {code(e): e.rules_pred for e in exs}
    people = {code(e) for e in exs if e.row.get("labeler") != "claude"}
    arms = {"parser (B1)": {c: {"pred": p, "sec": 0.0} for c, p in parser.items()}}
    for path in sorted(OUT.glob("*.jsonl")):
        arms[path.stem] = cache(path.stem)[1]
    codes = list(gold)
    base = arms.get("ft_small_s13")
    lines = [f"Dev: {len(codes)} reels ({len(people)} labeled by people). Exact = same place; tolerant = "
             "near-miss names count. Gate = answer agrees with the parser.", "",
             "| arm | exact | tolerant | people-labeled | vs FT small s13 | gate coverage | gate precision | sec/reel |",
             "|---|---|---|---|---|---|---|---|"]
    for name, got in arms.items():
        if len(got) < len(codes):
            lines.append(f"| {name} | partial {len(got)}/{len(codes)} | | | | | | |")
            continue
        hits = [same(got[c]["pred"], gold[c]) for c in codes]
        tol = sum(M.tolerant(got[c]["pred"], gold[c]) for c in codes)
        ph = [h for c, h in zip(codes, hits) if c in people]
        vs = ""
        if base and len(base) >= len(codes) and name != "ft_small_s13":
            b, a, p = ev.mcnemar_exact([same(base[c]["pred"], gold[c]) for c in codes], hits)
            vs = f"small only {b}, this only {a}, p={p:.3g}"
        gated = [c for c in codes if same(got[c]["pred"], parser[c])]
        gp = sum(M.tolerant(got[c]["pred"], gold[c]) for c in gated)
        sec = sum(got[c].get("sec") or 0.0 for c in codes) / len(codes)
        lines.append(f"| {name} | {ev.format_rate(sum(hits), len(codes))} | {tol}/{len(codes)} | "
                     f"{sum(ph)}/{len(ph)} | {vs} | {len(gated) / len(codes):.0%} | {gp}/{len(gated)} tolerant | {sec:.2f} |")
    text = "\n".join(lines)
    (OUT / "report.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["ft", "llm", "both", "ens", "report", "all"], default="all")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    exs = dev_examples()
    print(f"{len(exs)} dev reels", flush=True)
    if args.stage in ("ft", "all"):
        for name in FT:
            run_ft(name)
    if args.stage in ("ens", "all"):
        for name, members in ENSEMBLES.items():
            run_ensemble(name, members)
    if args.stage in ("llm", "all"):
        for llm in LLMS:
            run_llm(llm, exs)
    if args.stage in ("both", "all"):
        for llm in LLMS:
            for name in BOTH_FT:
                run_both(name, llm, exs)
    report(exs)


if __name__ == "__main__":
    main()
