"""Baseline span tagger: a CRF that tags the venue span in a caption.

Train: every train-split row (corpus gold + Nick's burner gold + Claude's burner silver).
Venue spans come from distant supervision (the labeled venue string, or its @handle, found
in the caption); rows whose venue can't be found are skipped. Score: the SAME original dev
reels the parser is scored on, end to end, with eval.py's lenient "same place" test.
Prints counts and shortcodes only.
"""
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
DATA = REPO / "data"
os.chdir(REPO)
sys.path.insert(0, str(REPO))
import sklearn_crfsuite  # noqa: E402
from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.config import IngestionSettings  # noqa: E402

exec((HERE / "alias_test.py").read_text(encoding="utf-8").split("arms = {")[0])  # reuse row building
norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
TOK = re.compile(r"@[\w.]+|#[\w]+|[\w'’&.-]+|📍|\S", re.U)
CTA = {"comment", "dm", "link", "bio", "recipe", "tickets", "rsvp", "code", "sponsored", "ad"}


def tokens(text):
    return [(m.group(), m.start(), m.end()) for m in TOK.finditer(text or "")]


def find_span(text, toks, venue):
    """Token indices of the venue in the caption: the name itself, else an @handle that spells it."""
    if not venue:
        return None
    m = re.search(re.escape(venue), text, re.I)
    if m:
        idx = [i for i, (_, s, e) in enumerate(toks) if s < m.end() and e > m.start()]
        return idx or None
    v = norm(venue)
    for i, (t, _, _) in enumerate(toks):
        if t.startswith("@") and len(v) >= 4 and (norm(t) == v or (v in norm(t) and len(norm(t)) - len(v) <= 6)):
            return [i]
    return None


def feats(toks, i):
    t = toks[i][0]
    f = {"b": 1.0, "low": t.lower(), "title": t.istitle(), "upper": t.isupper(), "digit": t.isdigit(),
         "handle": t.startswith("@"), "tag": t.startswith("#"), "pin": t == "📍", "suf3": t[-3:].lower(),
         "cta": t.lower().strip("“”\"") in CTA, "len": min(len(t), 12), "pos": min(i, 20) // 5}
    for d in (-3, -2, -1, 1, 2):
        j = i + d
        if 0 <= j < len(toks):
            u = toks[j][0]
            f[f"{d}:low"] = u.lower()
            f[f"{d}:pin"] = u == "📍"
            f[f"{d}:title"] = u.istitle()
            f[f"{d}:handle"] = u.startswith("@")
        else:
            f[f"{d}:edge"] = True
    f["pin_before"] = any(toks[j][0] == "📍" for j in range(max(0, i - 4), i))
    return f


def example(row):
    text = (row.get("input") or {}).get("caption") or ""
    toks = tokens(text)[:200]
    if not toks:
        return None
    g = row.get("gold") or {}
    labels = ["O"] * len(toks)
    if g.get("venue"):
        span = find_span(text, toks, g["venue"])
        if not span:
            return None                                   # venue not visible in the caption: skip
        span = [i for i in span if i < len(toks)]
        for k, i in enumerate(span):
            labels[i] = "B-VEN" if k == 0 else "I-VEN"
    elif g.get("in_catalog") is not False:
        return None                                       # unnamed place: no span to learn
    return [feats(toks, i) for i in range(len(toks))], labels, toks, text


def predict_venue(crf, row):
    text = (row.get("input") or {}).get("caption") or ""
    toks = tokens(text)[:200]
    if not toks:
        return None, 0.0
    X = [feats(toks, i) for i in range(len(toks))]
    marg = crf.predict_marginals_single(X)
    best, score, cur = None, 0.0, None
    for i, m in enumerate(marg):
        pb, pi = m.get("B-VEN", 0), m.get("I-VEN", 0)
        if pb > 0.5:
            cur = [i, i, pb]
        elif cur and pi > 0.5 and i == cur[1] + 1:
            cur[1] = i
        else:
            cur = None
        if cur and cur[2] > score:
            best, score = (cur[0], cur[1]), cur[2]
    if not best:
        return None, 0.0
    s, e = toks[best[0]][1], toks[best[1]][2]
    name = text[s:e].strip(" .,:-|")
    if name.startswith("@"):
        name = re.sub(r"[_.]+", " ", name[1:]).strip()
    return name or None, score


all_rows = corpus + gold_new + silver_new
train = [r for r in all_rows if ev.split_of(r) == "train" and (r.get("verdict") or {}).get("venue") != "needs_review"]
data = [x for x in (example(r) for r in train) if x]
print(f"train rows {len(train)} -> usable for spans {len(data)} "
      f"(with a venue span {sum('B-VEN' in x[1] for x in data)}, no-venue {sum('B-VEN' not in x[1] for x in data)})")
random.seed(0)
crf = sklearn_crfsuite.CRF(algorithm="lbfgs", c1=0.1, c2=0.1, max_iterations=150, all_possible_transitions=True)
crf.fit([x[0] for x in data], [x[1] for x in data])

dev = [r for r in corpus if ev.split_of(r) == "dev"]
ev.use_aliases("fit")          # train-only aliases, as eval.py does for --split dev (no leak)
parser ={r["code"]: r for r in ev.score(dev, use_llm=False, settings=IngestionSettings()).rows}
res = Counter()
scored = 0
for row in dev:
    g, v = row.get("gold", {}), row.get("verdict", {})
    if not (v.get("venue") in ev._SCOREABLE_VERDICTS and (g.get("venue") or g.get("in_catalog") is False)):
        continue
    scored += 1
    code = ev._code(row["url"])
    pv = parser[code]["pred_venue"]
    cv, conf = predict_venue(crf, row)
    same = lambda p: ev.venue_same_place(p, g.get("venue"))
    res["parser"] += same(pv)
    res["crf"] += same(cv)
    res["parser, crf if parser empty"] += same(pv or cv)
    res["crf if confident (>0.8), else parser"] += same(cv if cv and conf > 0.8 else pv)
    text = (row.get("input") or {}).get("caption") or ""
    visible = (not g.get("venue")) or bool(find_span(text, tokens(text), g["venue"]))
    res["ceiling: venue visible in caption"] += visible
    res["crf on visible-venue reels"] += visible and same(cv)
    res["parser on visible-venue reels"] += visible and same(pv)
    res["dev caption fidelity " + str(row.get("input_fidelity"))] += 1
print(f"\ndev reels scored: {scored}   (lenient: same place)")
for k in ("parser", "crf", "parser, crf if parser empty", "crf if confident (>0.8), else parser",
          "ceiling: venue visible in caption"):
    print(f"  {k:40} {ev.format_rate(res[k], scored)}")
vis = res["ceiling: venue visible in caption"]
for k in ("parser on visible-venue reels", "crf on visible-venue reels"):
    print(f"  {k:40} {ev.format_rate(res[k], vis)}")
print({k: v for k, v in res.items() if k.startswith("dev caption")})
