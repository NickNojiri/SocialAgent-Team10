"""End to end on ALL scoreable reels (not-a-place reels included): the parser, then every step
kept so far (rules 1c/2a/2d, places lookup 3a/3b), then the poster-role step (confident
'poster is the venue' -> the poster's name). Paired against the parser alone on dev."""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
exec((HERE / "places_ladder.py").read_text(encoding="utf-8").split("# 3c:")[0])   # sets, cur (after 3a/3b), base
import poster_role as PR  # noqa: E402  (trains the role model)

by_code = {ev._code(r["url"]): r for r in corpus + gold_new + silver_new}
THR = 0.7  # chosen on train + silver in poster_role.py


def role_step(x, v):
    row = by_code[x["code"]]
    f, out, cities = PR.features(row)
    p = PR.model.predict_proba(PR.vec.transform([f]))[0, 1]
    if p < THR:
        return v
    h, disp, names = PR.author_names(row)
    if not names or (v and any(ev.venue_same_place(v, n) for n in names)):
        return v
    mine = disp or names[0]
    if cities:
        mine = next((PR.P.exact(n, cities) or PR.P.prefix(n, cities) for n in names
                     if PR.P.exact(n, cities) or PR.P.prefix(n, cities)), mine)
    return mine


final = {k: [role_step(x, v) for x, v in zip(sets[k], cur[k])] for k in sets}
start = {k: [x["venue"] for x in sets[k]] for k in sets}
print("\n==== END TO END (all scoreable reels) ====")
for k in ("train", "silver", "dev"):
    xs = sets[k]
    print(f"{k:6} role step alone: {show('', cur[k], final[k], xs)[0]}")
    print(f"{k:6} everything vs parser: {show('', start[k], final[k], xs)[0]}   "
          f"tolerant {sum(tolerant(x, v) for x, v in zip(xs, start[k]))} -> {sum(tolerant(x, v) for x, v in zip(xs, final[k]))}")
for x, a, b in zip(sets["dev"], cur["dev"], final["dev"]):
    if a != b:
        print(f"   dev {x['code']}: {a!r} -> {b!r}  (gold {x['gold']!r})")
