"""B3, the LLM baseline: llama3.1:8b through the local Ollama server, zero-shot, no training.

For each dev reel, the prompt gives the prepared inputs (poster, location tag, caption, speech,
on-screen text) and asks for JSON {"venue": ..., "city": ..., "is_place": true/false}.
Temperature 0. Scored like every other model: same place + tolerant, McNemar vs the parser.
Nothing leaves the machine (Ollama runs locally).
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import clean_inputs as CI  # noqa: E402
import ml_reranker as M  # noqa: E402
import ranker_v3 as V  # noqa: E402
from burner_common import corpus, ev, nz  # noqa: E402

MODEL = "llama3.1:8b"
PROMPT = """You read an Instagram reel's text and say which single real-world place (restaurant, cafe, bar,
club, shop, venue) it is about. Answer ONLY with JSON: {{"venue": "<name or null>", "city": "<city or null>",
"is_place": true|false}}. Use null and is_place=false for recipes, ads, memes, lists of several places,
or when no specific place is named. Use the place's own name, not the poster's name, unless the
poster IS the place.

{ctx}"""


def ask(ctx):
    body = json.dumps({"model": MODEL, "prompt": PROMPT.format(ctx=ctx), "format": "json", "stream": False,
                       "options": {"temperature": 0, "num_ctx": 4096}}).encode()
    req = urllib.request.Request("http://localhost:11434/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        out = json.loads(json.loads(resp.read())["response"])
    v = out.get("venue")
    return v if (out.get("is_place") and isinstance(v, str) and v.strip() and v.lower() != "null") else None


def main():
    M.OPTS.update(clean=True, parser_pick=False, accounts=True)
    dev = [r for r in corpus if ev.split_of(r) == "dev" and V.scoreable(r)]
    hits, rules, tol, t0 = [], [], 0, time.time()
    for i, row in enumerate(dev, 1):
        prep = CI.enrich_row(row)
        try:
            pred = ask(M.context(prep))
        except Exception as exc:  # noqa: BLE001
            print(f"  {i}: ollama error {type(exc).__name__}", flush=True)
            pred = None
        gold = (row.get("gold") or {}).get("venue")
        parser = nz.normalize(ev.raw_from_input(row["url"], row.get("input") or {})).get("venue_name")
        hits.append(bool(ev.venue_same_place(pred, gold)))
        rules.append(bool(ev.venue_same_place(parser, gold)))
        tol += M.tolerant(pred, gold)
        if i % 10 == 0:
            print(f"  {i}/{len(dev)} ({time.time() - t0:.0f}s) running {sum(hits)}/{i}", flush=True)
    b, a, p = ev.mcnemar_exact(rules, hits)
    print(f"\nB3 Ollama {MODEL} on dev: {ev.format_rate(sum(hits), len(dev))} · tolerant {tol}/{len(dev)} · "
          f"parser {sum(rules)}/{len(dev)} · parser only {b}, LLM only {a}, McNemar p={p:.3g}")
    with (HERE.parents[1] / "data" / "experiments" / "ml_reranker_results.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"tag": "B3_ollama", "dev_ml": sum(hits), "dev_parser": sum(rules), "n_dev": len(dev),
                            "p": p, "dev_tolerant_ml": tol}) + "\n")


if __name__ == "__main__":
    main()
