"""One input-cleaning step for the parser and every model, and a test of what it changes.

clean_text(): NFKC Unicode normalization (𝐍𝐔𝐑𝐔 → NURU, ｆｕｌｌ → full), repair mojibake (ftfy),
zero-width "spaces" → a real space, drop other invisible characters (direction marks, soft
hyphen), unescape HTML entities. U+2028/2029 are kept (the repo's line convention).
clean_speech(): clean_text + drop Whisper boilerplate ('Thank you for watching', 'Music', ♪).
clean_screen(): clean_text + put spaces back into run-together OCR words (wordninja).
clean_row(): applies the above to a labeled row's input, keeps a country/state/big-city
location tag as a city hint only (location_city) instead of a venue candidate, and fills a
missing display name from the poster's Instagram profile when we have it.

    python experiments/burner_study/clean_inputs.py     # parser before vs after, train + dev
"""
import copy
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

import ftfy
import wordninja

HERE = Path(__file__).parent
REPO = HERE.parents[1]
SPACELIKE = re.compile(r"[​⁠ㅤ]")                  # zero-width / filler 'spaces' that separate words
INVISIBLE = re.compile(r"[‌‎‏﻿­]")      # direction marks, BOM, soft hyphen (ZWJ kept: emoji)
WHISPER_JUNK = re.compile(r"(?:\[?\s*(?:music|applause|laughter)\s*\]?|♪+|thanks? (?:you )?(?:so much )?for watching!?|"
                          r"please subscribe!?|subscribe!?|구독과 좋아요 부탁드려요!?|ご視聴ありがとうございました)", re.I)
GENERIC_TAG = re.compile(r"^(?:usa|united states|america|california|ca|earth|world|worldwide|planet earth|"
                         r"new york|los angeles|la|nyc|orange county|socal|southern california|texas|florida)$", re.I)


def clean_text(s):
    if not s:
        return s
    # Mojibake repair only. U+2028/2029 are left alone on purpose: the repo treats them as NOT a
    # line break (AGENTS.md: split on "\n", never splitlines()), and turning them into newlines
    # made the parser's list detector fire on single-venue posts (dev DeMlOAPx_Vp).
    s = ftfy.fix_text(s, uncurl_quotes=False, fix_line_breaks=False)
    s = SPACELIKE.sub(" ", s)
    s = INVISIBLE.sub("", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = html.unescape(s)
    # NFKC turns styled letters (math bold, fullwidth, circled) into plain ones. It would also turn
    # some emoji-adjacent symbols plain, which is fine; it leaves Korean/Japanese/Chinese intact.
    return unicodedata.normalize("NFKC", s)


def clean_speech(s):
    s = clean_text(s or "")
    s = WHISPER_JUNK.sub(" ", s)
    s = " ".join(s.split())
    return s if len(re.findall(r"\w+", s)) >= 3 else ""


def clean_screen(s):
    s = clean_text(s or "")
    out = []
    for tok in s.split():
        parts = wordninja.split(tok) if re.fullmatch(r"[A-Za-z]{12,}", tok) else [tok]
        # 'canconfirmthatthenew' -> 'can confirm that the new'; reject splits that strand a letter
        # ('calicocidery' -> 'calico cider y'): keep the token as it was.
        if len(parts) > 1 and all(len(p) > 1 or p.lower() in ("a", "i") for p in parts):
            out.extend(parts)
        else:
            out.append(tok)
    return " ".join(out)


def load_accounts():
    p = REPO / "data" / "experiments" / "accounts.jsonl"
    acc = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                a = json.loads(line)
                acc[a["username"].lower()] = a
    return acc


ACCOUNTS = load_accounts()


def _jsonl(name):
    p = REPO / "data" / "experiments" / name
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                d = json.loads(line)
                out.setdefault(d["code"], {}).update({k: v for k, v in d.items() if v not in (None, "", [])})
    return out


VIDEO = {**_jsonl("pilot_av.jsonl"), **_jsonl("av_run.jsonl"), **_jsonl("dev_av.jsonl")}
FRESH = {**{c: d for c, d in VIDEO.items() if d.get("caption")}, **_jsonl("recapture.jsonl")}


def enrich_row(row, use_accounts=True):
    """The prepared view of a reel that the parser and every model read: the full caption when the
    stored one was cut off, the location tag / coordinates / display name from a fresh fetch when
    missing, cleaned speech + on-screen text, then clean_row() over all of it."""
    r = copy.deepcopy(row)
    inp = r.setdefault("input", {})
    code = r["url"].rstrip("/").split("/")[-1]
    fresh = FRESH.get(code, {})
    if fresh.get("caption") and len(fresh["caption"]) > len(inp.get("caption") or "") + 20:
        inp["caption"] = fresh["caption"]                     # stored caption was only the first line
    for k in ("location_text", "lat", "lng", "author_name"):
        if not inp.get(k) and fresh.get(k) not in (None, ""):
            inp[k] = fresh[k]
    v = VIDEO.get(code, {})
    if v.get("transcript"):
        inp["transcript"] = clean_speech(v["transcript"])
    if v.get("ocr"):
        inp["ocr"] = clean_screen(v["ocr"])
    return clean_row(r) if use_accounts else _clean_row_no_accounts(r)


def _clean_row_no_accounts(row):
    saved = dict(ACCOUNTS)
    ACCOUNTS.clear()
    try:
        return clean_row(row)
    finally:
        ACCOUNTS.update(saved)


def clean_row(row):
    r = copy.deepcopy(row)
    inp = r.setdefault("input", {})
    for k in ("caption", "og_title", "author_name", "location_text", "transcript", "ocr", "venue_candidate"):
        if inp.get(k):
            inp[k] = clean_text(inp[k])
    tag = (inp.get("location_text") or "").strip()
    if tag and GENERIC_TAG.match(tag.split(",")[0].strip()):
        inp["location_city"] = tag                          # a city hint, not a venue
        inp["location_text"] = ""
    if not inp.get("author_name"):
        a = ACCOUNTS.get((inp.get("handle") or "").lstrip("@").lower())
        if a and a.get("full_name"):
            inp["author_name"] = clean_text(a["full_name"])
    return r


def main():
    sys.path.insert(0, str(HERE))
    from burner_common import corpus, ev, nz

    def parser_hits(rows):
        out = []
        for r in rows:
            pred = nz.normalize(ev.raw_from_input(r["url"], r.get("input") or {})).get("venue_name")
            out.append(bool(ev.venue_same_place(pred, (r.get("gold") or {}).get("venue"))))
        return out

    for split in ("train", "dev"):
        rows = [r for r in corpus if ev.split_of(r) == split and (r.get("verdict") or {}).get("venue") in ev._SCOREABLE_VERDICTS
                and ((r.get("gold") or {}).get("venue") or (r.get("gold") or {}).get("in_catalog") is False)]
        before = parser_hits(rows)
        after = parser_hits([clean_row(r) for r in rows])
        b, a, p = ev.mcnemar_exact(before, after)
        changed = sum(1 for r in rows if clean_row(r)["input"] != r["input"])
        print(f"{split:5} parser on raw inputs {sum(before)}/{len(rows)} -> cleaned inputs {sum(after)}/{len(rows)} "
              f"(fixed {a}, broke {b}, McNemar p={p:.3g}; {changed} reels' inputs changed)")
        for r, x, y in zip(rows, before, after):
            if x != y:
                raw = nz.normalize(ev.raw_from_input(r["url"], r.get("input") or {})).get("venue_name")
                new = nz.normalize(ev.raw_from_input(r["url"], clean_row(r).get("input") or {})).get("venue_name")
                print(f"    {split} {'FIX ' if y else 'BREAK'} {ev._code(r['url'])}: {raw!r} -> {new!r} (gold {r['gold'].get('venue')!r})")


if __name__ == "__main__":
    main()
