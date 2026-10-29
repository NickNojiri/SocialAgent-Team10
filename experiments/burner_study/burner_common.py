"""Shared setup for the burner-study experiments: the labeled rows (corpus + Nick's confirmed
burner reels + Claude's silver burner reels), the places lookup, and the cities a reel mentions."""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parents[1]
for p in (str(REPO), str(REPO / "experiments"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

_ns: dict = {"__file__": str(HERE / "alias_test.py"), "__name__": "alias_test_setup"}
exec((HERE / "alias_test.py").read_text(encoding="utf-8").split("arms = {")[0], _ns)
corpus, gold_new, silver_new = _ns["corpus"], _ns["gold_new"], _ns["silver_new"]

from places_lookup import places, squash  # noqa: E402
from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.pipeline import normalizer as nz  # noqa: E402

ev.use_aliases("fit")          # the parser sees train-only corrections (no dev leak)
P = places()


def cities_of(row, out):
    """Cities the reel itself mentions (parser geo, location tag, city names in the caption).
    Never the gold label."""
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
