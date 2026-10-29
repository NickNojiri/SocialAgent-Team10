"""Input audit: everything in the data that corrupts what the parser and the models read.

Covers the corpus (fixtures/labels.jsonl), the burner reels (labels.inbox.jsonl), transcripts and
on-screen text (data/experiments/*av*.jsonl), and the labels themselves. Prints counts and
a few shortcodes per problem; no captions are printed.
"""
import html
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from src.ingestion import eval as ev  # noqa: E402


def lines(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").split("\n") if l.strip()]


corpus = lines(REPO / "fixtures" / "labels.jsonl")
inbox = lines(REPO / "fixtures" / "labels.inbox.jsonl")
av = {}
for f in ("pilot_av.jsonl", "av_run.jsonl", "dev_av.jsonl"):
    p = REPO / "data" / "experiments" / f
    if p.exists():
        for d in lines(p):
            av[d["code"]] = d

STYLED = re.compile(r"[\U0001D400-\U0001D7FF！-～]")          # 𝐍𝐔𝐑𝐔 / ｆｕｌｌｗｉｄｔｈ letters
INVISIBLE = re.compile(r"[​-‏⁠﻿­]")
MOJIBAKE = re.compile(r"(?:Ã.|â€|ðŸ|Â[\x80-\xbf ])")
ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#\d+|#x[0-9a-f]+);", re.I)
TRUNC = re.compile(r"(?:…|\.\.\.)\s*(?:more)?\s*$", re.I)
LONG_TOKEN = re.compile(r"[A-Za-z]{16,}")
GENERIC_TAG = re.compile(r"^(?:usa|united states|america|california|ca|earth|world|worldwide|planet earth|"
                         r"new york|los angeles|la|nyc|orange county|socal|southern california|texas|florida)$", re.I)
WHISPER_JUNK = re.compile(r"^(?:\W|music|thanks? (?:you )?for watching|thank you|subscribe|you|bye|"
                          r"please subscribe|구독과 좋아요 부탁드려요|ご視聴ありがとうございました)*$", re.I)

issues = defaultdict(Counter)
examples = defaultdict(list)


def hit(scope, name, code):
    issues[scope][name] += 1
    if len(examples[(scope, name)]) < 4:
        examples[(scope, name)].append(code)


def check_text(scope, code, field, text):
    if not text:
        return
    if STYLED.search(text):
        hit(scope, f"{field}: styled unicode letters (𝐁𝐎𝐋𝐃 / ｆｕｌｌ) - regex & matching miss them", code)
    if INVISIBLE.search(text):
        hit(scope, f"{field}: invisible characters (zero-width, soft hyphen)", code)
    if "�" in text:
        hit(scope, f"{field}: replacement character U+FFFD (lost bytes)", code)
    if MOJIBAKE.search(text):
        hit(scope, f"{field}: mojibake (UTF-8 read as Latin-1)", code)
    if ENTITY.search(text):
        hit(scope, f"{field}: HTML entities (&amp; etc.)", code)
    if " " in text or " " in text:
        hit(scope, f"{field}: U+2028/2029 line separators", code)


seen_codes = Counter()
caption_owner = defaultdict(set)
for scope, rows in (("corpus", corpus), ("burner", inbox)):
    for r in rows:
        inp = r.get("input") or {}
        code = ev._code(r["url"])
        seen_codes[code] += 1
        cap = inp.get("caption") or ""
        if cap.strip():
            caption_owner[" ".join(cap.split())[:200].lower()].add(code)
        for field in ("caption", "og_title", "author_name", "location_text"):
            check_text(scope, code, field, inp.get(field) or "")
        if not cap.strip():
            hit(scope, "caption: empty", code)
        elif TRUNC.search(cap):
            hit(scope, "caption: looks truncated ('…' / '... more')", code)
        if r.get("input_fidelity") == "first_line_only":
            hit(scope, "caption: only the FIRST LINE was captured (input_fidelity)", code)
        if r.get("needs_recapture"):
            hit(scope, "flagged needs_recapture", code)
        if not inp.get("handle"):
            hit(scope, "handle: missing", code)
        if not inp.get("author_name"):
            hit(scope, "display name: missing", code)
        tag = (inp.get("location_text") or "").strip()
        if tag and GENERIC_TAG.match(tag.split(",")[0].strip()):
            hit(scope, "location tag: only a country/state/big city (no venue info)", code)
        if inp.get("lat") is not None and not tag:
            hit(scope, "coordinates without a location tag", code)
        if not inp.get("ocr") and not inp.get("transcript") and code not in av:
            hit(scope, "no speech / on-screen text captured", code)
        g = r.get("gold") or {}
        v = r.get("verdict") or {}
        gv = g.get("venue") or ""
        if v.get("venue") == "needs_review":
            hit(scope, "LABEL: still needs_review", code)
        if gv and " " not in gv and len(gv) >= 11 and gv[1:].islower():
            hit(scope, "LABEL: gold venue is a run-together word (handle?)", code)
        if gv and re.search(r"[#@📍]|https?://|\d{5}", gv):
            hit(scope, "LABEL: gold venue contains #/@/📍/link/zip", code)
        if gv and g.get("city") and gv.lower().endswith(" " + (g.get("city") or "").lower()):
            hit(scope, "LABEL: gold venue ends with its own city", code)
        pv = (r.get("predicted") or {}).get("venue")
        if v.get("venue") == "right" and gv and pv and not ev.venue_same_place(pv, gv):
            hit(scope, "LABEL: verdict 'right' but stored prediction differs from gold", code)
        if gv and g.get("in_catalog") is False:
            hit(scope, "LABEL: has a venue but in_catalog=False (contradiction)", code)

for code, n in seen_codes.items():
    if n > 1:
        hit("both", "same reel in corpus AND burner inbox (or twice)", code)
for cap, codes in caption_owner.items():
    if len(codes) > 1:
        for c in codes:
            hit("both", "near-identical caption on different reels (reposts)", c)

for code, d in av.items():
    t = " ".join((d.get("transcript") or "").split())
    o = d.get("ocr") or ""
    if t and WHISPER_JUNK.match(t):
        hit("video", "speech: Whisper boilerplate only ('Thank you for watching', 'Music')", code)
    words = re.findall(r"\w+", t.lower())
    if len(words) >= 20 and len(set(words)) / len(words) < 0.35:
        hit("video", "speech: highly repetitive (song lyrics / music)", code)
    if t:
        check_text("video", code, "speech", t)
    if o and LONG_TOKEN.search(o):
        hit("video", "screen text: words run together (OCR lost spaces)", code)
    if not d.get("had_video"):
        hit("video", "no video URL (photo post or fetch gap)", code)

for scope in ("corpus", "burner", "both", "video"):
    total = {"corpus": len(corpus), "burner": len(inbox), "both": len(seen_codes), "video": len(av)}[scope]
    print(f"\n== {scope} ({total} items) ==")
    for name, n in issues[scope].most_common():
        print(f"  {n:4}  {name}   e.g. {', '.join(examples[(scope, name)][:3])}")
