"""Learned category classifier vs today's keyword rules (Track B #16).

    python scripts/category_classifier.py                 # needs Ollama + mxbai-embed-large
    python scripts/category_classifier.py --policy model  # model on every reel, not only rule misses

Embeds each reel's text (caption, title, handle, location tag, hashtags, speech, on-screen
text) with the project's embedding model, learns one centroid per category from the
**train split only**, and scores the **dev split** three ways: the rules, the model, and
the default policy "rules first, model when the rules say `other`" (27 of the 43 old
category errors were reels no keyword matched). Each with its 95% interval, plus the
paired test against the rules (McNemar p and the interval of the difference).

Embeddings are cached in data/cache/category_embeddings.json (git-ignored), so a second
run is instant. The test split is never read here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline.normalizer import normalize  # noqa: E402

CACHE = REPO / "data" / "cache" / "category_embeddings.json"
Embed = Callable[[Sequence[str]], list[list[float]]]


def reel_text(row: dict) -> str:
    i = row.get("input") or {}
    parts = [i.get("caption"), i.get("og_title"), i.get("handle"), i.get("location_text"),
             " ".join(i.get("hashtags") or []), i.get("transcript"), i.get("ocr")]
    return " ".join(p for p in parts if p)[:2000]


def rules_category(row: dict) -> str:
    cand = normalize(ev.raw_from_input(row["url"], row.get("input") or {}))
    return getattr(cand.get("category"), "value", cand.get("category")) or "other"


def _unit(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def centroids(vectors: list[list[float]], labels: list[str]) -> dict[str, list[float]]:
    sums: dict[str, list[float]] = {}
    for v, y in zip(vectors, labels):
        u = _unit(v)
        s = sums.setdefault(y, [0.0] * len(u))
        for i, x in enumerate(u):
            s[i] += x
    return {y: _unit(s) for y, s in sums.items()}


def predict(cents: dict[str, list[float]], v: Sequence[float]) -> str:
    u = _unit(v)
    return max(cents, key=lambda y: sum(a * b for a, b in zip(cents[y], u)))


def ollama_embedder(settings: IngestionSettings) -> Embed:
    from src.ingestion.sinks.chroma_sink import OllamaEmbedder
    return OllamaEmbedder(settings)


def hashed_embedder(dim: int = 256) -> Embed:
    """A bag of hashed words. Only for tests and a dry run without Ollama: not the result."""
    def embed(texts):
        out = []
        for t in texts:
            v = [0.0] * dim
            for w in t.lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
            out.append(v)
        return out
    return embed


def cached(embed: Embed, name: str, path: Path = CACHE) -> Embed:
    store = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def run(texts):
        keys = [f"{name}:{hashlib.sha256(t.encode('utf-8')).hexdigest()}" for t in texts]
        missing = [t for t, k in zip(texts, keys) if k not in store]
        if missing:
            for t, v in zip(missing, embed(missing)):
                store[f"{name}:{hashlib.sha256(t.encode('utf-8')).hexdigest()}"] = v
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(store), encoding="utf-8", newline="\n")
        return [store[k] for k in keys]
    return run


def labeled(rows: list[dict], split: str) -> list[dict]:
    return [r for r in rows if ev.split_of(r) == split and (r.get("gold") or {}).get("category")
            and (r.get("verdict") or {}).get("category") in ("right", "wrong", "missing")]


def run(rows: list[dict], embed: Embed, policy: str = "hybrid") -> dict:
    train, dev = labeled(rows, "train"), labeled(rows, "dev")
    cents = centroids(embed([reel_text(r) for r in train]), [r["gold"]["category"] for r in train])
    gold = [r["gold"]["category"] for r in dev]
    rules = [rules_category(r) for r in dev]
    model = [predict(cents, v) for v in embed([reel_text(r) for r in dev])]
    chosen = model if policy == "model" else [m if r == "other" else r for r, m in zip(rules, model)]
    hits_r = [p == g for p, g in zip(rules, gold)]
    hits_m = [p == g for p, g in zip(model, gold)]
    hits_c = [p == g for p, g in zip(chosen, gold)]
    b_only, a_only, p = ev.mcnemar_exact(hits_r, hits_c)
    return {"train": len(train), "dev": len(dev), "rules": sum(hits_r), "model": sum(hits_m), "chosen": sum(hits_c),
            "policy": policy, "rules_only": b_only, "chosen_only": a_only, "p": p,
            "delta_ci": ev.paired_delta_interval(hits_r, hits_c), "per_class": dict(_per_class(gold, rules, chosen))}


def _per_class(gold, rules, chosen):
    out = defaultdict(lambda: [0, 0, 0])
    for g, r, c in zip(gold, rules, chosen):
        out[g][0] += 1
        out[g][1] += r == g
        out[g][2] += c == g
    return {k: tuple(v) for k, v in sorted(out.items())}


def report(res: dict, embedder_name: str) -> str:
    n = res["dev"]
    lines = [f"category on dev ({n} reels), centroids from {res['train']} train reels, embedder {embedder_name}",
             f"  rules             : {ev.format_rate(res['rules'], n)}",
             f"  model alone       : {ev.format_rate(res['model'], n)}",
             f"  {res['policy']:<17} : {ev.format_rate(res['chosen'], n)}"]
    ci = res["delta_ci"]
    lines.append(f"  change vs rules   : {(res['chosen'] - res['rules']) / n:+.1%}"
                 + (f" CI [{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "")
                 + f" · rules only {res['rules_only']}, new only {res['chosen_only']}, McNemar p={res['p']:.3g}")
    for k, (tot, r, c) in res["per_class"].items():
        lines.append(f"    {k:<14} {tot:>3} reels · rules {r} · {res['policy']} {c}")
    real = ci and ci[0] > 0
    lines.append("  verdict           : " + ("a real gain (interval above zero): worth wiring in" if real else
                                            "no proven gain: keep the rules"))
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", choices=["hybrid", "model"], default="hybrid")
    ap.add_argument("--embedder", choices=["ollama", "hashed"], default="ollama",
                    help="hashed = a dry run without Ollama (not a real result)")
    args = ap.parse_args(argv)
    settings = IngestionSettings()
    ev.use_aliases("fit")
    base = ollama_embedder(settings) if args.embedder == "ollama" else hashed_embedder()
    name = settings.embed_model if args.embedder == "ollama" else "hashed-words (dry run)"
    print(report(run(ev.load_labels(), cached(base, name), args.policy), name))


if __name__ == "__main__":
    main()
