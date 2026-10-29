"""Test 2: clean up the picked name after the pick (no retraining).

About 20 dev reels are the right place with the name a bit off ("Taco Land Lakewood" for
"Taco Land", "tacolandofficial"). Each rule below rewrites a predicted name:
  city_suffix    drop a trailing " - Lakewood" / " | LA" / " (Venice)" / " in Long Beach" when it is a known city
  word_city      drop trailing city words with no separator, only if what is left is a known place there
  official       drop a trailing "official" / "HQ"
  snap_prefix    snap to the known place the name starts with (places_lookup.prefix)
  snap_fuzzy     snap to a near-identical known place name in the reel's cities (ratio >= 92)

Which rules to keep is decided on TRAIN only: each rule is applied to the parser's picks on
gold-labeled train reels, and kept if it fixes at least 2 more than it breaks and breaks at most
half as many as it fixes. The kept rules are then applied once to every model's dev picks.
Writes compare/name_cleanup.md.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import compare_models as C  # noqa: E402
import ml_reranker as M  # noqa: E402
from burner_common import P, cities_of, corpus, ev, gold_new, nz, squash  # noqa: E402

SEP = re.compile(r"^(.+?)\s*(?:[-–—|•,@(]|\bin\b|\bat\b)\s*([^-–—|•,@()]+?)\)?\s*$", re.I)
OFFICIAL = re.compile(r"[\s_.]*(?:official|hq)$", re.I)


def city_suffix(name, cities):
    m = SEP.match(name)
    if m and squash(m.group(2)) in P.by_city and len(squash(m.group(1))) >= 3:
        return m.group(1).strip()
    return name


def word_city(name, cities):
    words = name.split()
    for k in (2, 1):
        if len(words) > k and squash(" ".join(words[-k:])) in P.by_city:
            rest = " ".join(words[:-k])
            if P.exact(rest, cities):
                return rest
    return name


def official(name, cities):
    rest = OFFICIAL.sub("", name)
    return rest if len(squash(rest)) >= 3 else name


def snap_prefix(name, cities):
    if not cities or P.exact(name, cities):
        return name
    return P.prefix(name, cities) or name


def snap_fuzzy(name, cities):
    if not cities or P.exact(name, cities):
        return name
    hit = P.fuzzy(name, cities, 92)
    return hit[0] if hit else name


RULES = {"city_suffix": city_suffix, "word_city": word_city, "official": official,
         "snap_prefix": snap_prefix, "snap_fuzzy": snap_fuzzy}


def reel_cities(row):
    return cities_of(row, nz.normalize(ev.raw_from_input(row["url"], row.get("input") or {})))


def apply(rules, name, cities):
    for r in rules:
        if name:
            name = RULES[r](name, cities)
    return name


def main():
    train = [r for r in corpus + gold_new if ev.split_of(r) == "train" and M.V.scoreable(r)]
    print(f"choosing rules on {len(train)} gold train reels (parser picks)", flush=True)
    picks = [(nz.normalize(ev.raw_from_input(r["url"], r.get("input") or {})).get("venue_name"), reel_cities(r),
              (r.get("gold") or {}).get("venue")) for r in train]
    lines = ["# Name cleanup after the pick", "", "## Rule choice on TRAIN (parser picks on gold train reels)", "",
             "| rule | changes a name | fixes | breaks | kept |", "|---|---|---|---|---|"]
    kept = []
    for name in RULES:
        changed = fixed = broke = 0
        for pred, cities, gold in picks:
            new = apply([name], pred, cities)
            if new == pred:
                continue
            changed += 1
            was, now = C.same(pred, gold), C.same(new, gold)
            fixed += (not was) and now
            broke += was and not now
        keep = fixed - broke >= 2 and broke <= fixed / 2
        kept += [name] if keep else []
        lines.append(f"| {name} | {changed} | {fixed} | {broke} | {'yes' if keep else 'no'} |")
    lines += ["", f"Kept, in this order: {', '.join(kept) or 'none'}", "",
              "## Effect on DEV (every model's picks, kept rules applied once)", "",
              "| arm | before (strict) | after | fixed | broke | McNemar p |", "|---|---|---|---|---|---|"]
    exs = C.dev_examples()
    cities = {C.code(e): reel_cities(e.row) for e in exs}
    gold = {C.code(e): e.gold for e in exs}
    for path in sorted(C.OUT.glob("*.jsonl")):
        got = C.cache(path.stem)[1]
        if len(got) < len(exs):
            continue
        codes = list(gold)
        before = [C.same(got[c]["pred"], gold[c]) for c in codes]
        after = [C.same(apply(kept, got[c]["pred"], cities[c]), gold[c]) for c in codes]
        b, a, p = ev.mcnemar_exact(before, after)
        lines.append(f"| {path.stem} | {sum(before)}/{len(codes)} | {sum(after)}/{len(codes)} | {a} | {b} | {p:.3g} |")
    text = "\n".join(lines)
    (C.OUT / "name_cleanup.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
