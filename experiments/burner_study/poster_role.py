"""Step 2 of the v2 plan: is the poster the venue itself, or a reviewer/creator?

Labels are free: for every reel whose label names a venue, the poster IS the venue exactly
when that venue is the same place as the account's handle or display name. Features are what
the post shows about its author (no gold): the parser's existing business-account signals,
whether the handle/display name is a real venue in a city the reel mentions (places lookup),
"we/our/us" vs "I/my/me", reviewer cues (follow me, invite/hosted, #ad), whether the display
name is a person's first name, blogger words in the handle.

Model: logistic regression, regularization chosen by 5-fold cross-validation on train grouped
by creator. Then the selection test: when the model is confident the poster is the venue,
use the poster's name as the venue. Decided on train + silver, confirmed on dev.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "experiments"))
sys.path.insert(0, str(HERE))
import gender_guesser.detector as gender  # noqa: E402
import ranker as R  # noqa: E402
from burner_common import P, cities_of, corpus, ev, gold_new, nz, silver_new, squash  # noqa: E402
from sklearn.feature_extraction import DictVectorizer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.model_selection import GroupKFold  # noqa: E402
G = gender.Detector(case_sensitive=False)
FIRST_SG = re.compile(r"\b(?:i|i'm|i’m|i've|my|me|mine)\b", re.I)
FIRST_PL = re.compile(r"\b(?:we|we're|we’re|our|ours|us)\b", re.I)
REVIEWER = re.compile(r"follow (?:me|@\w+|for more)|thank you (?:for having|@)|thanks for having|\binvite[d]?\b|"
                      r"\bhosted\b|#ad\b|\bpartner\b|\bcollab\b|\bugc\b|my (?:order|thoughts|favorite)|\bi tried\b|"
                      r"\bsave this\b|\bsend this\b|\btag (?:a|someone|your)\b", re.I)
BLOGGER = re.compile(r"eats|eat[sz]?$|foodie|food(?!s?hop)|bites|munch|explore|travel|adventure|diar|guide|recs|"
                     r"blog|life|withme|with[a-z]+$|^the[a-z]+(?:girl|guy|gal)|hungry|feast|nom|grub|snack", re.I)


def author_names(row):
    inp = row.get("input") or {}
    h = (inp.get("handle") or "").lstrip("@")
    names = [nz._handle_to_name(h)] if h else []
    disp = nz._clean_display_name(inp.get("author_name"))
    if disp:
        names.append(disp)
    return h, disp, [n for n in names if n]


def is_own(row):
    gold = (row.get("gold") or {}).get("venue")
    h, disp, names = author_names(row)
    if not gold or not h:
        return None
    g, s = squash(gold), squash(h)
    return any(ev.venue_same_place(n, gold) for n in names) or (len(g) >= 5 and (g in s or (len(s) >= 5 and s in g)))


def features(row):
    inp = row.get("input") or {}
    cap = inp.get("caption") or ""
    h, disp, names = author_names(row)
    out = nz.normalize(ev.raw_from_input(row["url"], inp))
    cities = cities_of(row, out)
    f = {k: v for k, v in R.poster_signals(row).items()}
    first = (disp or "").split()[0] if disp else ""
    f.update({
        "handle_is_place_here": float(any(P.exact(n, cities) or P.prefix(n, cities) for n in names) if cities else 0.0),
        "handle_is_place_anywhere": float(bool(P.exact(nz._handle_to_name(h)) or P.exact(disp or "")) if h else 0.0),
        "display_is_person": float(G.get_gender(first) not in ("unknown", "andy") and len((disp or "").split()) in (1, 2, 3)),
        "first_singular": min(len(FIRST_SG.findall(cap)), 4) / 4,
        "first_plural": min(len(FIRST_PL.findall(cap)), 4) / 4,
        "reviewer_cue": float(bool(REVIEWER.search(cap))),
        "blogger_word": float(bool(BLOGGER.search(h))),
        "mentions_other": float(any(m.lower() != h.lower() for m in nz._ANY_HANDLE.findall(cap))),
        "handle_in_caption": float(bool(h) and squash(h) in squash(cap)),
        "rules_pick_is_author": float(bool(out.get("venue_name")) and any(ev.venue_same_place(out.get("venue_name"), n) for n in names)),
        "has_location_tag": float(bool(inp.get("location_text"))),
    })
    return f, out, cities


def dataset(rows):
    X, y, groups, keep = [], [], [], []
    for r in rows:
        lab = is_own(r)
        if lab is None or (r.get("verdict") or {}).get("venue") == "needs_review":
            continue
        f, out, cities = features(r)
        X.append(f)
        y.append(int(lab))
        groups.append(ev.creator_of(r))
        keep.append((r, out, cities))
    return X, y, groups, keep


def fit_role(Xtr, ytr, gtr):
    """(vectorizer, model, C, CV accuracy): C chosen by 5-fold CV on train grouped by creator."""
    vec = DictVectorizer()
    A = vec.fit_transform(Xtr)
    best = None
    for C in (0.03, 0.1, 0.3, 1, 3, 10):
        accs = []
        for tr, te in GroupKFold(5).split(A, ytr, gtr):
            m = LogisticRegression(C=C, max_iter=2000).fit(A[tr], [ytr[i] for i in tr])
            accs.append(m.score(A[te], [ytr[i] for i in te]))
        if best is None or sum(accs) / 5 > best[1]:
            best = (C, sum(accs) / 5)
    return vec, LogisticRegression(C=best[0], max_iter=2000).fit(A, ytr), best[0], best[1]


def train_rows():
    return [r for r in corpus + gold_new + silver_new if ev.split_of(r) == "train"]


def report(name, y, p_own, thr=0.5):
    pred = [int(p >= thr) for p in p_own]
    tp = sum(1 for a, b in zip(y, pred) if a and b)
    fp = sum(1 for a, b in zip(y, pred) if not a and b)
    fn = sum(1 for a, b in zip(y, pred) if a and not b)
    acc = sum(1 for a, b in zip(y, pred) if a == b) / len(y)
    print(f"  {name:34} accuracy {acc:.3f} | poster-is-venue precision {tp / max(tp + fp, 1):.2f} recall {tp / max(tp + fn, 1):.2f}")


def main():
    Xtr, ytr, gtr, ktr = dataset(train_rows())
    Xdv, ydv, _, kdv = dataset([r for r in corpus if ev.split_of(r) == "dev"])
    vec, model, C, cv = fit_role(Xtr, ytr, gtr)
    A, B = vec.transform(Xtr), vec.transform(Xdv)
    print(f"train {len(ytr)} reels ({sum(ytr)} poster-is-venue) | dev {len(ydv)} ({sum(ydv)} poster-is-venue)")
    print(f"chosen C={C} (grouped 5-fold CV accuracy on train {cv:.3f})")
    print("\nPoster role on dev:")
    report("old rule: _is_business_account", ydv, [x["biz_account"] for x in Xdv])
    report("new classifier", ydv, model.predict_proba(B)[:, 1])
    w = sorted(zip(vec.get_feature_names_out(), model.coef_[0]), key=lambda t: -abs(t[1]))
    print("  strongest features:", ", ".join(f"{k} {v:+.2f}" for k, v in w[:8]))
    ptr = model.predict_proba(A)[:, 1]          # in-sample: only used to pick the threshold
    best_t = max((0.6, 0.7, 0.8, 0.9), key=lambda t: (lambda fb: fb[0] - fb[1])(apply(ktr, ptr, t)))
    f, b = apply(ktr, ptr, best_t)
    print(f"\nSelection test, threshold {best_t} chosen on train+silver: train fixed {f} broke {b}")
    f, b = apply(kdv, model.predict_proba(B)[:, 1], best_t)
    print(f"  dev (reels with a venue): fixed {f} broke {b}")


# Selection test: confident "poster is the venue" -> the poster's name (snapped to a real place
# when possible) becomes the venue, unless the rules already picked the poster.
def apply(kept, probs, thr):
    fixed = broke = 0
    for (r, out, cities), p in zip(kept, probs):
        gold = r["gold"]["venue"]
        pick = out.get("venue_name")
        if p < thr:
            continue
        h, disp, names = author_names(r)
        mine = disp or (names[0] if names else None)
        if cities and names:
            mine = next((P.exact(n, cities) or P.prefix(n, cities) for n in names if P.exact(n, cities) or P.prefix(n, cities)), mine)
        if not mine or (pick and any(ev.venue_same_place(pick, n) for n in names)):
            continue
        a, b = ev.venue_same_place(pick, gold), ev.venue_same_place(mine, gold)
        fixed += b and not a
        broke += a and not b
    return fixed, broke


if __name__ == "__main__":
    main()
