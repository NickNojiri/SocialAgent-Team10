"""Step 1 of the v2 plan: candidate recall by source. Is the right venue on the list at all?

For every reel with a gold/silver venue: which sources put the right place among their
candidates (eval.py's 'same place' test)? Sources are the ranker's caption sources
(experiments/ranker.py) plus the new ones: the places lookup (Overture names found in the
caption/tag, and near-miss names snapped to a real place), and for the transcribed burner
reels, speech and on-screen text. Prints counts only.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "experiments"))
sys.path.insert(0, str(HERE))
import ranker as R  # noqa: E402
from places_lookup import places, squash  # noqa: E402

exec((HERE / "alias_test.py").read_text(encoding="utf-8").split("arms = {")[0])  # corpus, gold_new, silver_new
ev.use_aliases("fit")
P = places()
nz = R.nz


def cities_of(row, out):
    geo = out.get("geo")
    names = list(getattr(geo, "place_names", None) or [])
    for t in (getattr(geo, "raw_location_text", None), (row.get("input") or {}).get("location_text")):
        if t:
            names += [p.strip() for p in t.split(",")]
    cap = (row.get("input") or {}).get("caption") or ""
    words = re.findall(r"[A-Za-z]+", cap)
    for n in (3, 2, 1):
        for i in range(len(words) - n + 1):
            c = squash(" ".join(words[i:i + n]))
            if len(c) >= 4 and c in P.by_city:
                names.append(" ".join(words[i:i + n]))
    return {squash(c) for c in names if c} & set(P.by_city)


def text_names(text, cities):
    """Names a free text offers: Title Case phrases, 'called/at/from X', and known places in it."""
    out = set()
    for m in R._TITLE_PHRASE.finditer(text or ""):
        out.add(m.group(1))
    for rx in (nz._AT_VENUE, nz._FROM_VENUE, nz._CALLED):
        for m in rx.finditer(text or ""):
            out.add(nz._trim_venue(m.group("venue")) or "")
    out |= set(P.names_in_text(text or "", cities, min_len=6))
    return {o for o in out if o}


def screen_names(ocr, cities):
    """OCR drops spaces ('comewithmetocalicocidery'): known places whose squashed name is in it."""
    s = squash(ocr)
    if len(s) < 6:
        return set()
    rows = P.city_rows(cities) or []
    return {P.df.at[i, "name"] for i in rows if len(P.df.at[i, "sq"]) >= 7 and P.df.at[i, "sq"] in s}


av = {}
for f in ("pilot_av.jsonl", "av_run.jsonl"):
    p = REPO / "data" / "experiments" / f
    if p.exists():
        for line in p.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                d = json.loads(line)
                av[d["code"]] = d


def audit(name, rows):
    hit = Counter()
    n = 0
    for r in rows:
        if not R.scoreable(r) or not (r.get("gold") or {}).get("venue"):
            continue
        gold = r["gold"]["venue"]
        out = nz.normalize(ev.raw_from_input(r["url"], r.get("input") or {}))
        cands = R.candidates(r, (out.get("venue_name"), out.get("venue_slot") or ""))
        cities = cities_of(r, out)
        n += 1
        by_src = {s: any(s in c.sources and ev.venue_same_place(c.name, gold) for c in cands) for s in R.SOURCES}
        ranker_any = any(by_src.values())
        snapped = {P.prefix(c.name, cities) for c in cands if cities} - {None}
        lookup = set(P.names_in_text(((r.get("input") or {}).get("caption") or "") + "\n" +
                                     ((r.get("input") or {}).get("location_text") or ""), cities)) | snapped
        new = {"lookup": any(ev.venue_same_place(x, gold) for x in lookup)}
        code = ev._code(r["url"])
        if code in av:
            new["speech"] = any(ev.venue_same_place(x, gold) for x in text_names(av[code].get("transcript"), cities))
            new["screen"] = any(ev.venue_same_place(x, gold) for x in screen_names(av[code].get("ocr"), cities))
            hit["(has audio/screen)"] += 1
        for s, ok in {**by_src, **new}.items():
            hit[s] += ok
        hit["ANY ranker source"] += ranker_any
        hit["ANY incl. new sources"] += ranker_any or any(new.values())
        for s, ok in new.items():
            hit[f"+{s} where ranker had nothing"] += ok and not ranker_any
    print(f"\n== {name}: {n} reels with a venue ==")
    for k, v in sorted(hit.items(), key=lambda kv: -kv[1]):
        print(f"  {k:38} {v:4}  ({100 * v / max(n, 1):.1f}%)")


audit("dev (gold)", [r for r in corpus if ev.split_of(r) == "dev"])
audit("train (gold)", [r for r in corpus if ev.split_of(r) == "train"])
burner = gold_new + silver_new
audit("burner reels WITH audio/screen (silver + Nick's gold)", [r for r in burner if ev._code(r["url"]) in av])
