"""Put the 633 original corpus reels on the review page for relabeling with the burner rubric.

Each reel gets its current label, the cleaned full caption, speech / on-screen text where we
transcribed it, and the poster's account type. A "check" flag (ordering only; the page never shows
what a model said) marks likely label mistakes:
  dev    both small M2 seeds disagree with the label (cached dev predictions; near misses ignored)
  train  M2 v5 (which trained on these reels and still can't fit the label) AND the parser disagree
  test   never flagged: comparing model answers with test labels would be a test read.
Order: dev + test first (they grade everything), earlier-Claude labels first, flagged first.
Writes <scratchpad>/reel_review/corpus.json.
"""
import json
import re
import sys
from pathlib import Path

from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import clean_inputs as CI  # noqa: E402
import compare_models as C  # noqa: E402
import ml_reranker as M  # noqa: E402
from burner_common import corpus, ev, nz  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("corpus.json")
KIND = {1: "Personal", 2: "Business", 3: "Creator"}
NOISE = re.compile(r"(?:\[?music\]?|♪+|thanks? (?:you )?for watching!?|\s|[.!])*", re.I)


def main():
    M.OPTS.update(clean=True, parser_pick=True, accounts=True, recipe_cue=True, llm_hint=False)
    dev_preds = [C.cache("ft_small_s13")[1], C.cache("ft_small_s14")[1]]
    train_rows = [r for r in corpus if ev.split_of(r) == "train" and M.V.scoreable(r)]
    d = M.DATA / "models" / C.FT["small_s13"][0]
    tok, model = AutoTokenizer.from_pretrained(d), AutoModelForSequenceClassification.from_pretrained(d).eval()
    train_exs = M.build(train_rows)
    train_pred = dict(zip((ev._code(r["url"]) for r in train_rows), M.evaluate(model, tok, train_exs, "cpu")[0]))
    out = []
    for row in corpus:
        code, split = ev._code(row["url"]), ev.split_of(row)
        gold = row.get("gold") or {}
        prep = CI.enrich_row(row)
        inp = prep.get("input") or {}
        venue = gold.get("venue")
        kind = "place" if venue else ("none" if gold.get("in_catalog") is False else "unnamed")
        check = False
        if split == "dev" and all(code in p for p in dev_preds):
            check = all(not M.tolerant(p[code]["pred"], venue) for p in dev_preds)
        elif split == "train" and code in train_pred:
            parser = nz.normalize(ev.raw_from_input(row["url"], row.get("input") or {})).get("venue_name")
            check = not M.tolerant(train_pred[code], venue) and not M.tolerant(parser, venue)
        handle = (inp.get("handle") or "").lstrip("@")
        a = M.ACCOUNTS.get(handle.lower())
        account = ""
        if a:
            cat = a.get("category_name") or a.get("business_category_name") or a.get("category") or ""
            account = (KIND.get(a.get("account_type")) or "Account") + (f" · {cat}" if cat else "")
        speech = " ".join((inp.get("transcript") or "").split())
        out.append({"code": code, "url": row["url"], "corpus": True, "split": split, "labeler": row.get("labeler") or "unknown",
                    "check": check, "handle": handle, "account": account,
                    "caption": (inp.get("caption") or "")[:2000].replace("�", ""),
                    "location": inp.get("location_text") or inp.get("location_city") or "",
                    "speech": "" if NOISE.fullmatch(speech) else speech[:600], "screen": (inp.get("ocr") or "")[:300],
                    "kind": kind, "venue": venue, "city": gold.get("city"), "category": gold.get("category") or "other",
                    "why": "other", "unsure": False, "rechecked": False})
    rank = lambda o: (o["split"] == "train", o["labeler"] != "claude", not o["check"], o["code"])
    for i, o in enumerate(sorted(out, key=rank)):
        o["order"] = i
    # U+FFFD (undecodable bytes in some captures) blocks publishing; it carries no text anyway.
    OUT.write_text(json.dumps(out, ensure_ascii=False).replace("�", ""), encoding="utf-8", newline="\n")
    by = lambda s: sum(1 for o in out if o["split"] == s)
    print(f"{len(out)} corpus reels -> {OUT} | dev {by('dev')} test {by('test')} train {by('train')} | "
          f"earlier-Claude labels {sum(o['labeler'] == 'claude' for o in out)} | "
          f"flagged: dev {sum(o['check'] for o in out if o['split'] == 'dev')}, train {sum(o['check'] for o in out if o['split'] == 'train')}")


if __name__ == "__main__":
    main()
