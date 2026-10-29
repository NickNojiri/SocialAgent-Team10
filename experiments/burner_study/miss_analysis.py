"""Train only: reels where the right venue IS on the candidate list but the rules picked
something else (or nothing). What does the right candidate look like vs the rules' pick?"""
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import ranker_v3 as V  # noqa: E402

train_rows = [r for r in V.corpus + V.gold_new if V.ev.split_of(r) == "train" and V.scoreable(r)]
rp = V.role_probs(train_rows, [])
buckets, srcs = Counter(), Counter()
examples = []
for row in train_rows:
    r = V.Reel(row, rp[V.ev._code(row["url"])])
    if r.target < 0 or V.ev.venue_same_place(r.rules_pred, r.gold):
        continue
    if r.target == len(r.cands):
        b = "gold: not a place · rules picked a venue"
    elif not r.rules_pred:
        b = "rules picked nothing · answer on the list"
    else:
        b = "rules picked the wrong name · answer on the list"
    buckets[b] += 1
    if r.target < len(r.cands):
        c = r.cands[r.target]
        for s in c.sources:
            srcs[s] += 1
        f = r.feats[r.target]
        examples.append((b[:22], r.rules_pred, c.name, sorted(c.sources), round(f["role_own_x_poster"] + f["role_own_x_other"], 2),
                         int(f["lk_here"] or f["lk_prefix_here"]), V.ev._code(row["url"])))
    else:
        examples.append((b[:22], r.rules_pred, None, [], 0, 0, V.ev._code(row["url"])))
print(dict(buckets))
print("sources of the right candidate:", srcs.most_common())
for e in examples:
    print(f"  [{e[0]}] rules {e[1]!r} | right {e[2]!r} {e[3]} | role {e[4]} | linked {e[5]} | {e[6]}")
