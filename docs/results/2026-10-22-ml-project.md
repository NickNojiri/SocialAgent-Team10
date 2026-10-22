# SpotBot venue extraction as a machine-learning project (2026-10-22)

How the "which place is this reel about?" step is set up as a supervised learning problem:
the task, the data and its splits, the baselines (B0–B3), the models (M1, M2), and for each one
the prediction rule, the loss and the optimizer. It also covers how results are evaluated and
why the project started with rules. Numbers are on the dev set (106 scorable reels) unless
stated otherwise. Scripts are in `experiments/burner_study/`.

## 1. The task is classification

Input: one reel, meaning its caption, location tag, poster handle, display name and account
type, plus speech and on-screen text when the video was transcribed. The project predicts
four things:

| What we predict | Kind of problem | Classes |
|---|---|---|
| **Venue** (the place the reel is about) | **Multiclass classification over each reel's own candidates** (listwise ranking), or equivalently **token classification** | the reel's candidate names + "no venue" / tokens O, B-VEN, I-VEN |
| Post type | Multiclass | place, list, venue later (promoter), not a place, can't tell |
| Poster role | Binary | venue's own account vs reviewer/creator |
| Category | Multi-label | food & drink, cafe & dessert, nightlife, live music, market & pop-up, outdoors, community, other |

**Why venue isn't a plain fixed-class problem.** Venue names are an open set: millions of
places, and most dev venues appear nowhere in train. A softmax over a fixed list of venues
can't predict a venue it has never seen. The two standard fixes:

1. **Token classification.** Label each word O / B-VEN / I-VEN (the "BIO" scheme), then read
   the venue off the tagged words. Each word is a 3-class decision.
2. **Candidate classification.** First list the names the reel could be about (@mentions,
   📍 lines, location tag, the poster, title-case phrases, places-database matches). Then
   classify *which one* it is, plus a "no venue" option. The class set is different for every
   reel, so the model scores each candidate and a softmax runs over that reel's options.

Both are multiclass classification. B2 and M1 use approach 1. M2 uses approach 2, the one
used in recent entity-linking research.

## 2. Why the project started with rules (B1)

The parser (`pipeline/normalizer.py`) is hand-written rules: "a 📍 line names the venue",
"'from @handle' names the venue", and so on. It came first for three reasons:

- With ~300 labels at the start, rules beat learned models. A model needs enough examples
  of each pattern to learn it.
- Rules are B1, the baseline every model has to beat. Without one you can't tell whether a
  model learned anything.
- The rule work also served as error analysis: it showed that the right answer is on the
  candidate list for 88% of dev reels, but the rules pick it only 65% of the time. That
  finding motivates M2.

The rules should step back as labels grow. M2 learns the same patterns from data, plus the
ones nobody wrote a rule for.

## 3. Data

| Source | Labels | Count |
|---|---|---|
| `fixtures/labels.jsonl` | gold (a person checked it) | 618 |
| Burner reels Nick confirmed on the review page | gold | 72 |
| Burner reels Claude labeled | **silver** (machine label, not checked) | 781 |

Gold can be scored. Silver is training-only, because its errors are the labeler's errors.
During training, silver gets a lower weight than gold (below).

**Inputs added for the models:** speech (Whisper "small", local) and on-screen text (OCR)
for 142 low-text burner reels and the dev reels; Instagram account type for posters.

### Input audit and cleaning (`input_audit.py`, `missing_inputs.py`, `clean_inputs.py`)

What was wrong with the inputs, across all reels:

| Problem | Count | Fix |
|---|---|---|
| Poster display name missing | 440 of 633 corpus reels (dev 70, train 245) | filled from the poster's Instagram profile (burner job) |
| Caption cut off after the first line / flagged for recapture | dev 2, train 12, test 12 | full caption re-fetched for dev + train (test kept as-is so its baseline stays comparable) |
| Location tag is only a country / state / big city | 90 | kept as a city hint, not offered as a venue |
| Invisible characters (zero-width spaces, soft hyphens) | 77 | zero-width space → space, others removed |
| Styled Unicode letters (𝐍𝐔𝐑𝐔, ｆｕｌｌ) | 47 | NFKC normalization |
| Mojibake / lost bytes | ~5 | ftfy repair |
| OCR words run together | 36 of 164 | word segmentation (wordninja), rejected when it strands letters |
| Whisper boilerplate ("Thank you for watching", "Music") | 19 of 164 | removed |
| Repost crossing test and train | 1 group of 4 reels | train copies to be dropped from training before any test read |
| Answer-key issues | 2 dev labels to check, 4 dev reels still needs-review | for a person to fix |

U+2028/2029 line separators are deliberately **left as they are**: the repo treats them as not
a line break, and converting them made the parser call a single-venue post a list.

Effect of text cleaning on the parser alone: train 246 → 247 (1 fixed, 0 broken), dev 69 → 69.
The parser was already tolerant of dirty characters. The inputs that matter are the **missing**
ones (display names, full captions, account type, speech, screen text), and those come from
the burner job. The ML runs measure them as ablations: raw → cleaned + parser pick → + account type.

## 4. Splits

| | Train | Dev | Test |
|---|---|---|---|
| Gold | 365 (53%) | 121 (18%) | 204 (30%) |
| Gold + silver | 1,146 (78%) | 121 (8%) | 204 (14%) |

- **Grouped by creator.** All of a creator's reels sit in one split, so a model can't learn
  a creator's style or venues in train and get credit for them in dev or test (leakage).
- **Frozen** in `fixtures/splits.json`. New creators are hashed 60/20/20 until dev and test
  reach ~500 reels each. A 10% split today would be ~69 reels, a ±11-point confidence
  interval: too noisy to compare models.
- **Validation set:** 15% of *train* creators, held out for early stopping and model choices.
  **Dev** compares finished models. **Test** is read once at the end, logged in
  `docs/results/TEST_LEDGER.md`.

## 5. Baselines and models

| ID | What | Learns? | Dev (same place) |
|---|---|---|---|
| **B0** | Naive: first @mention, else location tag, else "no venue" | no | **40.6%** (43/106) |
| **B1** | The rule parser (`normalizer.py`) | no (hand rules + a learned alias table) | **65.1%** (69/106) |
| **B2** | **RNN:** BiLSTM token tagger trained from scratch | yes | **36.8%** (39/106) |
| **B3** | **Ollama** llama3.1:8b, zero-shot, local | no (pretrained, prompted) | *(next)* |
| — | CRF token tagger (classic sequence model) | yes | 26.4% |
| — | Softmax ranker, hand-made features (`ranker_v3.py`) | yes | 65.1% |
| **M1** | Transformer token tagger (DistilBERT-multilingual, fine-tuned) | yes | 41.5% |
| **M2** | **Transformer cross-encoder re-ranker (mMiniLM, fine-tuned)** | yes | *(training)* |

### B2: RNN (BiLSTM tagger)
- **Input:** a word sequence (caption, location tag, speech, screen text). Each word is a
  learned 100-d embedding plus 6 shape features (Title-case, ALL CAPS, digit, @, #, 📍).
- **Model:** embedding → bidirectional LSTM (128 units each way) → dropout 0.3 → linear → 3 logits per word.
- **Prediction:** softmax per word over {O, B-VEN, I-VEN}. The venue is the contiguous
  B/I run with the highest mean probability; none → "no venue".
- **Loss:** token-level cross-entropy, venue tokens weighted ×5 (they're rare), padding ignored.
- **Optimizer:** Adam, learning rate 2e-3, batch 32, gradient clipping 5, up to 20 epochs,
  early stopping (patience 4) on the validation creators.
- **Training labels:** distant supervision. The labeled venue string (or its @handle) is
  located in the text and those words become B/I.

### B3: Ollama (zero-shot LLM)
- llama3.1:8b on this machine. The prompt gives the reel's text and asks for JSON
  `{venue, city, is_place}`. No training. This is the "just ask an LLM" baseline the team
  wanted compared. Earlier work in this repo found a small local model as a *gap filler*
  made results worse; B3 measures it as a stand-alone extractor.

### M1: transformer tagger
Same task and decoding as B2, but the encoder is pretrained DistilBERT-multilingual,
fine-tuned for 4 epochs (cross-entropy, AdamW 5e-5). 41.5%: beats the CRF, loses to the parser.

### M2: transformer cross-encoder re-ranker (main model)
- **Candidates** for each reel: every name the reel offers (rules' pick, @mentions, 📍,
  location tag, poster, title-case phrases, places-lookup matches), up to 12, plus `[NO VENUE]`.
  The right answer is on this list for 88% of dev reels (the ceiling).
- **Input pair** for each candidate *c*: `(c, context)`, where context = `poster: @handle
  (display name) [account type] | location tag | caption | said in video | on screen`,
  truncated to 256 tokens.
- **Scoring function:** s<sub>θ</sub>(c, x) = wᵀ·h<sub>[CLS]</sub>(c ⊕ x) + b, where h is the
  multilingual MiniLM encoder (12 layers, pretrained on multilingual search ranking) and θ
  are all the weights.
- **Prediction:** ŷ = argmax<sub>c ∈ options(x)</sub> s<sub>θ</sub>(c, x); `[NO VENUE]` winning means "not a place".
- **Probability:** p(c | x) = softmax over the reel's options of s<sub>θ</sub>.
- **Loss:** cross-entropy (negative log-likelihood of the right option), weighted per reel:
  L(θ) = − (1/N) Σ<sub>reels</sub> w<sub>r</sub> · log p(y<sub>r</sub> | x<sub>r</sub>),
  with w = 1 for gold and 0.5 for silver. Reels whose answer is not on the list are skipped
  (nothing to learn from).
- **Optimizer:** AdamW (Adam with decoupled weight decay 0.01), learning rate 2e-5, linear
  warm-up over the first 10% of steps then linear decay, gradient clipping at 1.0, and
  gradient accumulation (8 reels per update, since each reel's batch is its candidate list).
- **Regularization and model selection:** weight decay, dropout inside the encoder, and
  early stopping: keep the epoch with the best accuracy on the validation creators.
- **Learning curve:** retrain on 25 / 50 / 75 / 100% of training creators and plot dev
  accuracy. A curve still rising at 100% means more labels will help; a flat one means the
  model or its inputs are the limit.

## 6. Evaluation

- **Metric:** accuracy, where a prediction is right when it names the same place as the gold
  label (`eval.venue_same_place`, so "Taco Land" = "taco land"). A second **tolerant** score
  also accepts a name that contains the other ("Sweet Bee West Covina" vs "Sweet Bee"),
  per Nick's rule that near-misses are fine.
- **Uncertainty:** 95% Wilson confidence intervals on each accuracy, and a 95% interval on
  the paired difference between two models.
- **Significance:** the McNemar exact test on the reels where the two models disagree. It's
  paired: both models see the same reels.
- "Can't tell" reels are left out of scoring, and their count is reported. The test split is
  read once, at the end.

## 7. Where the points are (dev, today's parser)

| Bucket | Reels | Points | Which part of the system fixes it |
|---|---|---|---|
| Right today | 69 | 65.1 | |
| Right place, name a bit off | 9 | 8.5 | name normalization + places lookup; tolerant scoring |
| Answer is the poster | 9 | 8.5 | M2 with account type (poster role) |
| Answer is the location tag / other caption text | 7 | 6.6 | M2 |
| Not a place, but a venue was named | 5 | 4.7 | M2's `[NO VENUE]` option + post-type classifier |
| Name in no text we read | 7 | 6.6 | speech + on-screen text as M2 input |

## 8. Results (filled in as runs finish)

| Model | Dev same place | Dev tolerant | vs B1 (McNemar p) |
|---|---|---|---|
| B0 naive | 40.6% | 53/106 | |
| B1 parser | 65.1% | 78/106 | |
| B2 BiLSTM | 36.8% | | p < 0.001 (worse: B1 right on 37 reels it missed, it right on 7) |
| B3 Ollama | *pending* | | |
| M1 transformer tagger | 41.5% | 64/106 | |
| M2 cross-encoder | *pending* | | |
