"""Find answer-key mistakes: run the parser, M2 (v5) and Ollama on every train + dev corpus reel,
and flag a reel when all three agree on an answer that differs from its label.

Also draws an equal-size random CONTROL sample of un-flagged reels, so the blind re-check can
move labels in either direction (checking only flagged reels would bias every fix toward the
models) and measures the answer key's general error rate.

Test is excluded on purpose (comparing predictions with test labels is a test read).

Writes
  data/experiments/keycheck.json          blind items for the review page (no answers in it)
  data/experiments/keycheck_answers.json  predictions + current labels, for analysis afterwards
"""
import json
import random
import sys
import time
from pathlib import Path

from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import b3_ollama as B3  # noqa: E402
import clean_inputs as CI  # noqa: E402
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev, nz  # noqa: E402

EXP = HERE.parents[1] / "data" / "experiments"
MODEL = HERE.parents[1] / "data" / "models" / "reranker_v5_moregold_frac1.0"


def same(a, b):
    return (not a and not b) or bool(a and b and ev.venue_same_place(a, b))


def main():
    rows = [r for r in corpus if ev.split_of(r) in ("train", "dev") and M.V.scoreable(r)]
    print(f"{len(rows)} train + dev corpus reels", flush=True)
    M.OPTS.update(clean=True, parser_pick=True, accounts=True, recipe_cue=True)   # v5's inputs
    tok, model = AutoTokenizer.from_pretrained(MODEL), AutoModelForSequenceClassification.from_pretrained(MODEL).eval()
    exs = M.build(rows)
    m2_preds, _ = M.evaluate(model, tok, exs, "cpu")
    out, t0 = [], time.time()
    for i, (row, ex, m2) in enumerate(zip(rows, exs, m2_preds), 1):
        prep = CI.enrich_row(row)
        parser = nz.normalize(ev.raw_from_input(row["url"], prep["input"])).get("venue_name")
        M.OPTS["parser_pick"] = False                 # Ollama sees the reel, not the parser's answer
        try:
            llm = B3.ask(M.context(prep))
        except Exception:  # noqa: BLE001
            llm = "<error>"
        M.OPTS["parser_pick"] = True
        gold = (row.get("gold") or {}).get("venue")
        agree3 = llm != "<error>" and same(parser, m2) and same(m2, llm)
        flag = agree3 and not same(m2, gold)
        out.append({"code": ev._code(row["url"]), "split": ev.split_of(row), "labeler": row.get("labeler"),
                    "gold": gold, "parser": parser, "m2": m2, "ollama": llm, "flag": flag, "prep": prep})
        if i % 25 == 0:
            print(f"  {i}/{len(rows)} ({time.time() - t0:.0f}s), flagged so far {sum(o['flag'] for o in out)}", flush=True)

    flagged = [o for o in out if o["flag"]]
    rest = [o for o in out if not o["flag"]]
    control = random.Random(23).sample(rest, min(len(rest), len(flagged)))
    items = []
    for o in flagged + control:
        inp = o["prep"]["input"]
        items.append({"code": o["code"], "url": f"https://www.instagram.com/reel/{o['code']}/",
                      "handle": (inp.get("handle") or "").lstrip("@"), "author": inp.get("author_name") or "",
                      "caption": " ".join((inp.get("caption") or "").split())[:900],
                      "location": inp.get("location_text") or inp.get("location_city") or "",
                      "speech": (inp.get("transcript") or "")[:500], "screen": (inp.get("ocr") or "")[:250]})
    random.Random(29).shuffle(items)                   # flagged and control reels look the same
    (EXP / "keycheck.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8", newline="\n")
    answers = [{k: v for k, v in o.items() if k != "prep"} | {"control": o in control} for o in flagged + control]
    (EXP / "keycheck_answers.json").write_text(json.dumps(answers, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    by = {s: sum(1 for o in flagged if o["split"] == s) for s in ("train", "dev")}
    print(f"\nflagged {len(flagged)} (train {by['train']}, dev {by['dev']}) + control {len(control)} -> keycheck.json")
    agree = sum(1 for o in out if same(o["parser"], o["m2"]) and same(o["m2"], o["ollama"]))
    print(f"all three methods agree on {agree} of {len(out)} reels")


if __name__ == "__main__":
    main()
