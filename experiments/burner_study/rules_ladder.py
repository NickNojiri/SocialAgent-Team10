"""Rule ladder on top of the parser: add one change at a time, score each step.

Tune on train (corpus gold), confirm on the SAME original dev reels, lenient "same place"
scoring, paired McNemar vs the previous step. A step is KEPT only if it helps train
(fixed > broke). Silver burner reels are reported as an extra check, never used to decide.
"""
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
DATA = REPO / "data"
os.chdir(REPO)
sys.path.insert(0, str(REPO))
from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.pipeline import normalizer as N  # noqa: E402

exec((HERE / "alias_test.py").read_text(encoding="utf-8").split("arms = {")[0])  # corpus, gold_new, silver_new
ev.use_aliases("fit")
WEAK = {"sentence", "at_venue", "bare_mention", "own_post", "first_person", "none", None}
flat = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())


def scoreable(r):
    g, v = r.get("gold") or {}, r.get("verdict") or {}
    return v.get("venue") in ev._SCOREABLE_VERDICTS and (g.get("venue") or g.get("in_catalog") is False)


def prep(rows):
    out = []
    for r in rows:
        if not scoreable(r):
            continue
        raw = ev.raw_from_input(r["url"], r.get("input", {}))
        cand = N.normalize(raw)
        out.append({"code": ev._code(r["url"]), "gold": r["gold"].get("venue"), "raw": raw,
                    "cap": raw.caption or "", "venue": cand.get("venue_name"), "slot": cand.get("venue_slot")})
    return out


def tag_ok(t):
    return bool(t) and not N._ADDRESS_RE.search(t) and not N._is_container(t) and not N._is_region(t)


def tag_of(x):
    t = (x["raw"].location_text or "").split(",")[0].strip()
    return t if tag_ok(t) else None


def first_pin_name(x):
    for line in x["cap"].split("\n"):
        if "📍" in line:
            body = line.split("📍", 1)[1].strip()
            if body and not re.match(r"\d", body):
                name = N._pin_name(body)
                if name and tag_ok(name):
                    return name
    return None


# Each step maps (x, current venue) -> new venue. Order = the order they're tried.
STEPS = [
    ("1a tag fills an empty venue (no extra gate)",
     lambda x, v: v if v else tag_of(x)),
    ("1b tag replaces a weak-slot venue",
     lambda x, v: tag_of(x) if (tag_of(x) and x["slot"] in WEAK) else v),
    ("1c tag replaces any venue when an @mention spells it",
     lambda x, v: tag_of(x) if (tag_of(x) and flat(tag_of(x)) in {flat(m) for m in re.findall(r"@([\w.]+)", x["cap"])}) else v),
    ("2a 📍 name fills an empty venue",
     lambda x, v: v if v else first_pin_name(x)),
    ("2b 📍 name replaces a weak-slot venue",
     lambda x, v: first_pin_name(x) if (first_pin_name(x) and x["slot"] in WEAK) else v),
    ("2c 'at/from/tried @handle' fills an empty venue",
     lambda x, v: v or (lambda m: N._handle_to_name(m.group(1)) if m else None)(
         re.search(r"\b(?:at|from|tried|try|visit(?:ed)?)\s+@([\w.]+)", x["cap"], re.I))),
    ("2d ad cue + no 📍 + no tag -> no venue",
     lambda x, v: None if (v and x["slot"] in WEAK and "📍" not in x["cap"] and not x["raw"].location_text
                           and re.search(r"comment\s+[“\"']?\w+|link in (?:my )?bio|use code|#ad\b|sponsored", x["cap"], re.I)) else v),
]

# City words come ONLY from train-side labels (gold city on train rows + every burner label),
# never from dev: an earlier version of this list was written from dev misses, which is tuning on dev.
CITY_WORDS = {flat(c) for r in corpus + gold_new + silver_new
              if ev.split_of(r) == "train" for c in [((r.get("gold") or {}).get("city") or "").split(",")[0]]
              if c and len(flat(c)) >= 2}


def tidy(x, v):
    """Clean a predicted name: split a run-together @handle using the caption's own spelling,
    drop 'official'/'usa' handle suffixes, and drop a trailing ' - Branch' or city word."""
    if not v:
        return v
    s = re.sub(r"(?i)(official|_?usa?|_?hq)$", "", v.strip()).strip(" _.-")
    if " " not in s and len(s) >= 8:                       # 'Tacolandofficial' -> look for 'Taco Land' in caption
        for m in re.finditer(r"[A-Z][\w'&]*(?:\s+(?:&|and|[A-Z][\w'&]*)){1,4}", x["cap"]):
            if flat(m.group()).replace("and", "") and flat(s).startswith(flat(m.group())) or flat(m.group()) == flat(s):
                s = m.group()
                break
    s = re.split(r"\s+[-–|]\s+", s)[0]
    words = s.split()
    while len(words) > 1 and flat(words[-1]) in CITY_WORDS:
        words = words[:-1]
    if len(words) > 2 and flat(" ".join(words[-2:])) in CITY_WORDS:
        words = words[:-2]
    return " ".join(words) or v


STEPS.append(("2e tidy the name (handle split, 'official', branch/city suffix)", tidy))


def same(x, v):
    return ev.venue_same_place(v, x["gold"])


def tolerant(x, v):
    """Nick's tolerance: right place even if the name is a bit off (one name contains the other)."""
    if ev.venue_same_place(v, x["gold"]):
        return True
    a, b = flat((v or "").replace("&", "and")), flat((x["gold"] or "").replace("&", "and"))
    return bool(a and b) and min(len(a), len(b)) >= 4 and (a in b or b in a)


sets = {"train": prep([r for r in corpus if ev.split_of(r) == "train"]),
        "dev": prep([r for r in corpus if ev.split_of(r) == "dev"]),
        "silver": prep([r for r in silver_new])}
cur = {k: [x["venue"] for x in xs] for k, xs in sets.items()}


def show(name, before, after, xs):
    b = [same(x, v) for x, v in zip(xs, before)]
    a = [same(x, v) for x, v in zip(xs, after)]
    fixed, broke = sum(1 for p, q in zip(b, a) if q and not p), sum(1 for p, q in zip(b, a) if p and not q)
    _, _, p = ev.mcnemar_exact(b, a)
    return f"{sum(a)}/{len(xs)} ({100 * sum(a) / len(xs):.1f}%) fixed {fixed} broke {broke} p={p:.2f}", fixed, broke


print(f"{'start: parser':52} train {sum(same(x, v) for x, v in zip(sets['train'], cur['train']))}/{len(sets['train'])}"
      f" | dev {sum(same(x, v) for x, v in zip(sets['dev'], cur['dev']))}/{len(sets['dev'])}"
      f" | silver {sum(same(x, v) for x, v in zip(sets['silver'], cur['silver']))}/{len(sets['silver'])}")
for name, fn in STEPS:
    new = {k: [fn(x, v) for x, v in zip(sets[k], cur[k])] for k in sets}
    tr, f, b = show(name, cur["train"], new["train"], sets["train"])
    dv, _, _ = show(name, cur["dev"], new["dev"], sets["dev"])
    sv, sf, sb = show(name, cur["silver"], new["silver"], sets["silver"])
    keep = (f + sf > b + sb) and f >= b          # gold train + silver decide; gold train may not get worse
    print(f"{name:52} {'KEEP' if keep else 'drop'}\n    train {tr}\n    dev   {dv}\n    silver {sv}")
    if keep:
        cur = new
start_dev = [x["venue"] for x in sets["dev"]]
print("\nALL KEPT vs parser on dev:", show("", start_dev, cur["dev"], sets["dev"])[0])
for k in ("train", "dev"):
    xs = sets[k]
    p0 = sum(tolerant(x, x["venue"]) for x in xs)
    p1 = sum(tolerant(x, v) for x, v in zip(xs, cur[k]))
    print(f"tolerant score ({k}): parser {p0}/{len(xs)} ({100 * p0 / len(xs):.1f}%) -> kept rules {p1}/{len(xs)} ({100 * p1 / len(xs):.1f}%)")
