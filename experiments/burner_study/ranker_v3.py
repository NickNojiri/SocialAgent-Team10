"""Ranker v3: the team's candidate ranker (experiments/ranker.py) + the signals it was missing.

experiments/ranker.py v2 found the answer among its candidates for ~86% of dev reels but tied
the rules (65.1%): its features only describe the caption. v3 adds, per candidate:
  - poster role: P(poster is the venue) from poster_role.py's classifier (out-of-fold on train),
    crossed with "this candidate is the poster" / "it isn't"
  - places lookup: is the name a real venue in a city the reel mentions (exact / prefix-snap /
    anywhere); lookup names and snapped names also join the candidate list
and, for the "no venue" option: P(poster is the venue), promoter cues (text/RSVP/tickets/link in
bio), and "no candidate is a real place".

Same softmax over each reel's options (candidates + "no venue"), fit with L-BFGS. L2 strength and
the weight given to Claude's silver labels are chosen by 5-fold CV on train, grouped by creator
(a creator's reels are never split across folds). Scored on the SAME dev reels as everything
else, paired McNemar against the rules. Test is not read.
"""
import re
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import poster_role as PR  # noqa: E402
import ranker as R  # noqa: E402
from burner_common import P, cities_of, corpus, ev, gold_new, nz, silver_new, squash  # noqa: E402
from sklearn.model_selection import GroupKFold  # noqa: E402

# From the train-only miss analysis (miss_analysis.py): the rules often pick a run-together
# handle when the location tag / display name spells the same place properly; a neighborhood or
# a piece of app/service text; and miss the venue's own account when its caption is short.
JUNK = re.compile(r"\b(?:parking|valet|picks?|doordash|uber ?eats|grubhub|postmates|delivery|menu|link|bio|"
                  r"reservations?|giveaway|promo|code)\b", re.I)
PROMOTER = re.compile(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}|\b(?:txt|text)\s+[“\"']?\w+|\brsvp\b|tickets?|link in (?:my )?bio|"
                      r"\bpresale\b|\blineup\b|\bline up\b", re.I)


# ── poster-role probabilities (out-of-fold for train rows) ─────────────────────
def role_probs(train, others):
    X, y, g, keep = PR.dataset(train)
    vec, model, C, _ = PR.fit_role(X, y, g)
    p = {}
    A = vec.transform(X)
    oof = np.zeros(len(y))
    for tr, te in GroupKFold(5).split(A, y, g):
        m = PR.LogisticRegression(C=C, max_iter=2000).fit(A[tr], [y[i] for i in tr])
        oof[te] = m.predict_proba(A[te])[:, 1]
    for (r, _, _), q in zip(keep, oof):
        p[ev._code(r["url"])] = q
    for r in train + others:                      # rows without a venue label, and dev: full model
        code = ev._code(r["url"])
        if code not in p:
            f, _, _ = PR.features(r)
            p[code] = model.predict_proba(vec.transform([f]))[0, 1]
    return p


# ── reels: candidates + features ───────────────────────────────────────────────
class Reel:
    def __init__(self, row, role_p):
        inp = row.get("input") or {}
        cap = inp.get("caption") or ""
        out = nz.normalize(ev.raw_from_input(row["url"], inp))
        self.row, self.gold = row, (row.get("gold") or {}).get("venue")
        self.rules_pred = out.get("venue_name")
        self.silver = row.get("source") == "silver"
        cities = cities_of(row, out)
        cands = R.candidates(row, (out.get("venue_name"), out.get("venue_slot") or ""))
        found = {R.sq(c.name): c for c in cands}

        def add(name, src):
            key = R.sq(name)
            if len(key) >= 3 and len(name) <= 60:
                found.setdefault(key, R.Candidate(name)).sources.add(src)

        if cities:
            for n in P.names_in_text(cap + "\n" + (inp.get("location_text") or ""), cities):
                add(n, "lookup")
            for c in list(found.values()):
                s = P.prefix(c.name, cities)
                if s and R.sq(s) != R.sq(c.name):
                    add(s, "snap")
        self.cands = list(found.values())
        none = not out.get("venue_name")
        self.feats = []
        linked_any = False
        pick_sq = R.sq(out.get("venue_name"))
        pick_smashed = bool(out.get("venue_name")) and " " not in out["venue_name"].strip() and len(pick_sq) >= 8
        short = len(cap) < 80
        for c in self.cands:
            f = R.features(c, row, none)
            here = bool(cities) and P.exact(c.name, cities) is not None
            pre = (not here) and bool(cities) and P.prefix(c.name, cities) is not None
            anywhere = len(R.sq(c.name)) >= 6 and P.exact(c.name) is not None
            linked_any |= here or pre
            own = float(f["is_poster"] or f["is_display_name"] or "author" in c.sources)
            f.update({
                "src_lookup": float("lookup" in c.sources), "src_snap": float("snap" in c.sources),
                "lk_here": float(here), "lk_prefix_here": float(pre), "lk_anywhere": float(anywhere),
                "lk_here_when_rules_none": float(here) * float(none),
                "role_own_x_poster": role_p * own, "role_rev_x_poster": (1 - role_p) * own,
                "role_own_x_other": role_p * (1 - own),
                # a spaced, proper spelling of a run-together pick ('Stadiumpepz' -> 'Stadium Pepz Pizza')
                "better_spelling_of_pick": float(pick_smashed and " " in c.name.strip()
                                                 and R.sq(c.name) != pick_sq
                                                 and (R.sq(c.name).startswith(pick_sq[:6]) or pick_sq.startswith(R.sq(c.name)[:6]))),
                "is_smashed_pick": float(pick_smashed and R.sq(c.name) == pick_sq),
                "is_locality": float(R.sq(c.name) in P.by_city),
                "junk_words": float(bool(JUNK.search(c.name))),
                "poster_x_short_caption": own * float(short),
                "poster_x_rules_none": own * float(none),
            })
            self.feats.append(f)
        nf = R.none_features(row, out)
        nf.update({"none_role_own": role_p, "none_promoter": float(bool(PROMOTER.search(cap))),
                   "none_no_linked": float(not linked_any), "none_n_cands": min(len(self.cands), 10) / 10})
        self.none_f = nf
        if not self.gold:
            self.target = len(self.cands)
        else:
            self.target = next((j for j, c in enumerate(self.cands) if ev.venue_same_place(c.name, self.gold)), -1)

    def matrix(self, keys):
        rows = [[f.get(k, 0.0) for k in keys] for f in self.feats] + [[self.none_f.get(k, 0.0) for k in keys]]
        return np.array(rows, dtype=float)

    def pick(self, theta, keys):
        s = self.matrix(keys) @ theta
        j = int(np.argmax(s))
        return self.cands[j].name if j < len(self.cands) else None


def scoreable(row):
    return R.scoreable(row)


def fit(reels, keys, l2, silver_w):
    data = [(r.matrix(keys), r.target, silver_w if r.silver else 1.0) for r in reels if r.target >= 0]
    data = [d for d in data if d[2] > 0]

    def loss(theta):
        total, grad = 0.0, np.zeros_like(theta)
        for X, t, w in data:
            s = X @ theta
            s -= s.max()
            e = np.exp(s)
            p = e / e.sum()
            total -= w * np.log(p[t] + 1e-12)
            g = p.copy()
            g[t] -= 1
            grad += w * (X.T @ g)
        n = sum(w for _, _, w in data)
        return total / n + l2 * theta @ theta, grad / n + 2 * l2 * theta

    res = minimize(loss, np.zeros(len(keys)), jac=True, method="L-BFGS-B", options={"maxiter": 300})
    return res.x


def accuracy(reels, theta, keys):
    return [bool(ev.venue_same_place(r.pick(theta, keys), r.gold)) for r in reels]


def main():
    train_rows = [r for r in corpus + gold_new + silver_new if ev.split_of(r) == "train" and scoreable(r)]
    dev_rows = [r for r in corpus if ev.split_of(r) == "dev" and scoreable(r)]
    print(f"rows: train {len(train_rows)} (silver {sum(r.get('source') == 'silver' for r in train_rows)}) | dev {len(dev_rows)}")
    rp = role_probs(train_rows, dev_rows)
    train = [Reel(r, rp[ev._code(r["url"])]) for r in train_rows]
    dev = [Reel(r, rp[ev._code(r["url"])]) for r in dev_rows]
    keys = sorted({k for r in train + dev for f in r.feats + [r.none_f] for k in f})
    gold_train = [r for r in train if not r.silver]

    # CV on train, grouped by creator; scored on the GOLD reels of the held-out creators only.
    creators = sorted({ev.creator_of(r.row) for r in train})
    fold = {c: i % 5 for i, c in enumerate(np.random.RandomState(491).permutation(creators))}
    best = None
    for silver_w in (0.0, 0.5, 1.0):
        for l2 in (0.001, 0.01, 0.1):
            right = n = 0
            for k in range(5):
                fit_on = [r for r in train if fold[ev.creator_of(r.row)] != k]
                held = [r for r in gold_train if fold[ev.creator_of(r.row)] == k]
                th = fit(fit_on, keys, l2, silver_w)
                right += sum(accuracy(held, th, keys))
                n += len(held)
            cv = right / n
            print(f"  CV  silver weight {silver_w:.1f}  l2 {l2:<6} -> {cv:.3f} on {n} gold train reels")
            if best is None or cv > best[0]:
                best = (cv, silver_w, l2)
    cv, silver_w, l2 = best
    theta = fit(train, keys, l2, silver_w)
    print(f"\nchosen: silver weight {silver_w}, l2 {l2} (CV {cv:.3f})")

    for name, reels in (("gold train", gold_train), ("dev", dev)):
        rules = [bool(ev.venue_same_place(r.rules_pred, r.gold)) for r in reels]
        ours = accuracy(reels, theta, keys)
        b_only, a_only, p = ev.mcnemar_exact(rules, ours)
        ci = ev.paired_delta_interval(rules, ours)
        oracle = sum(r.target >= 0 for r in reels)
        print(f"\n{name}: {len(reels)} reels · an option holds the answer for {ev.format_rate(oracle, len(reels))}")
        print(f"   rules     : {ev.format_rate(sum(rules), len(reels))}")
        print(f"   ranker v3 : {ev.format_rate(sum(ours), len(reels))}")
        print(f"   change    : {(sum(ours) - sum(rules)) / len(reels):+.1%}"
              + (f" CI [{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "") + f" · rules only {b_only}, v3 only {a_only}, McNemar p={p:.3g}")
    w = sorted(zip(keys, theta), key=lambda t: -abs(t[1]))
    print("\nstrongest weights:", ", ".join(f"{k} {v:+.2f}" for k, v in w[:12]))
    if "--show" in sys.argv:
        for r in dev:
            a, b = r.rules_pred, r.pick(theta, keys)
            if bool(ev.venue_same_place(a, r.gold)) != bool(ev.venue_same_place(b, r.gold)):
                print(f"   {ev._code(r.row['url'])}: rules {a!r} | v3 {b!r} | gold {r.gold!r}")


if __name__ == "__main__":
    main()
