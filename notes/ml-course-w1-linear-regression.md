# ML course — Week 1: linear regression, cost function

Quiz taken 2026-09-21. Model: **f_w,b(x) = wx + b**

## Vocabulary (most of the quiz is just this)

| Symbol | Name | Role |
|---|---|---|
| `x` | feature / input | what you feed the model |
| `y` | target / output | the true answer you're trying to predict |
| `f_w,b(x)` or `ŷ` | prediction | what the model outputs for a given x |
| `w`, `b` | **parameters** (weights, bias) | the knobs training adjusts |
| `m` | number of training examples | size of the dataset, not an input |
| `(x, y)` | one training example | an input paired with its target |
| `J(w, b)` | cost function | how wrong the model is, averaged over the training set |

The trap in every one of these questions is confusing the four roles:
**input (x) · target (y) · parameter (w, b) · dataset size (m).**

## Answers

**Q1 — J(w,b) very close to zero, what can you conclude?**
→ *The parameters cause the algorithm to fit the **training set** really well.*
J is the squared error over the training data, so small J means predictions are
close to targets **on those points**. "Never possible / must be a bug" is wrong —
J = 0 is achievable (a line through two points hits both exactly).

**Q2 — which are the inputs/features fed into the model?**
→ `x`. Not `m` (dataset size), not `w, b` (parameters, learned not given),
not `(x, y)` (that's a full example — y is the answer, you don't feed it in at
prediction time).

**Q3 — when does the model fit the data relatively well?**
→ *When the cost J is at or near a minimum.* "Fits well" is **defined** as low
cost. `w` being near zero means nothing on its own — it just makes a flat line.

**Q4 — which is the output or "target" variable?**
→ `y`.

**Q5 — which are the parameters that can be adjusted?**
→ `w` **and** `b`. Fixing b = 0 forces the line through the origin, which is an
arbitrary restriction that usually fits worse.

## The one thing worth remembering past the quiz

Q1's answer is "fits the **training set** well" — *not* "is a good model."
Low training cost and a good model are different claims, and the gap between
them is overfitting.

This is not abstract for us. In SpotBot's extractor, the learned alias table was
built from the whole labeled corpus, including the held-out rows it was then
scored on — training-set memorisation wearing a test-set badge. Reported venue
accuracy was 49.6%; with the test rows' own labels removed it was **37.4%**.

> Low J on data the model has seen proves it memorised. Only data it has never
> seen can say whether it learned.

See `docs/ML_REVIEW_QUESTIONS.md` (finding 1) and
`python scripts/eval_diagnostics.py` for the measured version.
