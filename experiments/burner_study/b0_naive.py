"""B0, the naive baseline: no learning, no parser. Predict the first @mention in the caption
(spelled as a name), else the location tag, else "no venue". Any model must beat this."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ranker_v3 as V  # noqa: E402
from burner_common import corpus, ev, nz, squash  # noqa: E402


def b0(row):
    inp = row.get("input") or {}
    cap = inp.get("caption") or ""
    author = (inp.get("handle") or "").lstrip("@").lower()
    for h in nz._ANY_HANDLE.findall(cap):
        if h.lower() != author:
            return nz._handle_to_name(h)
    tag = (inp.get("location_text") or "").split(",")[0].strip()
    return tag or None


def tolerant(pred, gold):
    if ev.venue_same_place(pred, gold):
        return True
    a, b = squash((pred or "").replace("&", "and")), squash((gold or "").replace("&", "and"))
    return bool(a and b) and min(len(a), len(b)) >= 4 and (a in b or b in a)


dev = [r for r in corpus if ev.split_of(r) == "dev" and V.scoreable(r)]
hits = [bool(ev.venue_same_place(b0(r), (r.get("gold") or {}).get("venue"))) for r in dev]
tol = sum(tolerant(b0(r), (r.get("gold") or {}).get("venue")) for r in dev)
print(f"B0 naive on dev: {ev.format_rate(sum(hits), len(dev))} · tolerant {tol}/{len(dev)}")
