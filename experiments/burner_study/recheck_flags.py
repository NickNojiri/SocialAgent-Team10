"""Find burner-review answers worth a second look, from the reel's own evidence only.

No model predictions are used (flagging by model disagreement would pull the labels toward
the model and inflate its score). Each rule compares Nick's saved answer with the reel's text:
  not_in_text      a venue answer that appears nowhere in caption / speech / screen / tag /
                   handle / display name (not even as a near match)
  place_cue        "not a place" or "list" although the reel has a 📍 pin, an address, or a
                   location tag naming a business (not just a city)
  business_poster  "not a place" although the poster is a Business account
  fast_bulk        answered inside a burst (>= 10 answers within 60 s), and has a place cue
  spelling         a fixed venue name that is close to, but not the same as, a name in the text
  later_address    "venue later" although the caption gives a street address
Writes data/experiments/recheck_candidates.json for Claude's read-through.
    python experiments/burner_study/recheck_flags.py <reels.json> <decisions dir>
"""
import json
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from places_lookup import places, squash  # noqa: E402

ADDR = re.compile(r"\b\d{2,6}\s+(?:[NSEW]\.?\s+)?[\w.']+(?:\s+[\w.'#]+){0,5}?\s+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Way|Ln|Lane|Pkwy|Parkway|Hwy|Ct|Pl)\b", re.I)
PIN = re.compile(r"📍\s*[:\-–|]?\s*([^\n📞⏰🕒🗓#|•]{3,80})")


def near(name, text):
    """Best similarity of `name` to any same-length stretch of `text` (both collapsed)."""
    n, t = squash(name), squash(text)
    if not n or not t:
        return 0.0
    if n in t:
        return 1.0
    m = SequenceMatcher(autojunk=False)
    m.set_seq2(n)
    best = 0.0
    for size in (len(n) - 1, len(n), len(n) + 1):
        for i in range(max(1, len(t) - size + 1)):
            m.set_seq1(t[i:i + size])
            if m.real_quick_ratio() > best and m.quick_ratio() > best:
                best = max(best, m.ratio())
    return best


def main():
    reels = {r["code"]: r for r in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))}
    decs = {}
    for p in Path(sys.argv[2]).glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        decs[p.stem] = d.get("data", d)
    P = places()
    times = sorted((datetime.fromisoformat(d["at"].replace("Z", "+00:00")), c) for c, d in decs.items() if d.get("at"))
    burst = set()
    for i, (t, c) in enumerate(times):
        window = [x for x in times[max(0, i - 10):i + 11] if abs((x[0] - t).total_seconds()) <= 30]
        if len(window) >= 10:
            burst.add(c)
    out = []
    for code, d in decs.items():
        r = reels.get(code)
        if not r:
            continue
        v, venue = d.get("verdict"), d.get("venue")
        text = " ".join(str(r.get(k) or "") for k in ("caption", "speech", "screen", "location", "handle"))
        loc = (r.get("location") or "").strip()
        loc_is_business = bool(loc) and squash(loc) not in P.by_city and squash(loc.split(",")[0]) not in P.by_city \
            and squash(loc) not in {"usa", "unitedstates", "california", "newyork", "losangeles"}
        pins = [m.group(1).strip() for m in PIN.finditer(r.get("caption") or "")]
        place_cue = [f"pin: {p}" for p in pins[:2]] + (["has a street address"] if ADDR.search(r.get("caption") or "") else []) \
            + ([f"location tag: {loc}"] if loc_is_business else [])
        why = []
        if v in ("ok", "fixed") and venue:
            score = near(venue, text)
            if score < 0.85:
                why.append(f"not_in_text ({score:.2f})")
            elif v == "fixed" and score < 1.0:
                why.append(f"spelling ({score:.2f})")
        if v in ("none", "list") and place_cue:
            why.append("place_cue: " + "; ".join(place_cue))
        if v == "none" and (r.get("account") or "").startswith("Business"):
            why.append(f"business_poster ({r['account']})")
        if code in burst and place_cue and v in ("none", "list"):
            why.append("fast_bulk")
        if v == "later" and ADDR.search(r.get("caption") or ""):
            why.append("later_address")
        if why:
            out.append({"code": code, "split": r.get("split"), "answer": d, "why": why, "handle": r.get("handle"),
                        "account": r.get("account"), "location": loc, "caption": (r.get("caption") or "")[:700],
                        "speech": (r.get("speech") or "")[:300], "screen": (r.get("screen") or "")[:200]})
    path = HERE.parents[1] / "data" / "experiments" / "recheck_candidates.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    from collections import Counter
    print(f"{len(decs)} answers, {len(out)} flagged ->", path)
    print(Counter(w.split(" ")[0].rstrip(":") for o in out for w in o["why"]))
    print("in bursts:", len(burst))


if __name__ == "__main__":
    main()
