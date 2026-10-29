"""Run the local LLM (B3: llama3.1:8b, zero-shot, free text) on every train + dev reel and save
its answer, so M2 can use it as an extra input and candidate (ml_reranker.py --llm-hint).

The LLM sees the same prepared reel the review page shows, but NOT the parser's pick, so its
mistakes stay independent of the parser's. Train reels are included because the re-ranker has
to learn how much to trust the hint. Test reels are skipped (a later final run adds them).

Resumable: answers append to data/experiments/llm_hints.jsonl, one per reel.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import b3_ollama as B3  # noqa: E402
import clean_inputs as CI  # noqa: E402
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev, gold_new, silver_new  # noqa: E402

OUT = M.DATA / "experiments" / "llm_hints.jsonl"


def main():
    done = set(M.load_jsonl(OUT, "code"))
    rows = [r for r in corpus + gold_new + silver_new if ev.split_of(r) in ("train", "dev") and M.V.scoreable(r)]
    todo = [r for r in rows if ev._code(r["url"]) not in done]
    print(f"{len(rows)} train + dev reels, {len(todo)} still to ask", flush=True)
    M.OPTS.update(clean=True, parser_pick=False, accounts=True, recipe_cue=True)
    errors, t0 = 0, time.time()
    for i, row in enumerate(todo, 1):
        try:
            venue = B3.ask(M.context(CI.enrich_row(row)))
        except Exception as exc:  # noqa: BLE001 - not saved, so a rerun asks again
            errors += 1
            print(f"  {ev._code(row['url'])}: ollama error {type(exc).__name__}", flush=True)
            if errors >= 5:
                raise SystemExit("5 Ollama errors in a row - stopping (is the server up?)")
            continue
        errors = 0
        with OUT.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"code": ev._code(row["url"]), "venue": venue, "model": B3.MODEL}, ensure_ascii=False) + "\n")
        if i % 50 == 0:
            print(f"  {i}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    got = M.load_jsonl(OUT, "code")
    print(f"done: {len(got)} answers ({sum(1 for d in got.values() if d.get('venue'))} name a place)")


if __name__ == "__main__":
    main()
