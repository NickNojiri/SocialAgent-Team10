# Burner reels: what moves venue accuracy (2026-10-22)

853 reels were pulled from the burner's DMs and given first-pass labels (silver). Nick
confirmed 72 of them on the review page (gold). Each experiment below scores the **same 113
original dev reels**; 106 of them have a scorable venue. Venue is scored with eval.py's
"same place" test. The correction table is train-only, so no dev answer leaks into the
extractor that scores it. The test split was not read. Scripts: `experiments/burner_study/`.
Inputs are in `data/` (gitignored). Hypothesis written before the first run:
`data/experiments/alias_test_hypothesis.md`.

## 1. More labels through the alias table: about zero

| Labels feeding the train-only alias table | Aliases | Dev venue (exact) | Dev same place |
|---|---|---|---|
| Today's corpus | 49 | 48/106 | 69/106 (65.1%) |
| + 72 confirmed burner reels | 59 | 48/106 | 69/106 |
| + all 853 burner reels (72 gold + 781 silver) | 160 | 49/106 | 68/106 |

Aliases only fire when the same venue comes back, and only 12 burner venues appear in dev.
Category and city can't move this way, because aliases touch only the venue. The one "loss"
is a bad dev label: DXR9yLHESKQ's gold is the smashed handle "Dumplingbarsanmarino".

## 2. A learned baseline: a CRF span tagger

Trained on 902 train reels: corpus gold, burner gold, and burner silver. Venue spans come
from distant supervision.

| System | Dev same place |
|---|---|
| Parser | 69/106 (65.1%) |
| CRF baseline | 28/106 (26.4%) |
| **Ceiling: the venue appears in the caption / @mentions at all** | **78/106 (73.6%)** |

26% of dev reels never name the venue in text, so no caption-only model can get them. On the
78 reels where the venue is visible, the parser gets 59. The CRF is the floor for any learned
model.

## 3. Rule ladder on top of the parser

Each step is decided on train before dev is looked at. A step is kept if gold train plus
silver fix more than they break, and gold train alone does not get worse.

| Step | Train (gold) fixed/broke | Silver fixed/broke | Kept |
|---|---|---|---|
| 1a location tag fills an empty venue | 0/0 | 5/13 | no |
| 1b location tag replaces a weak-slot venue | 1/12 | 23/44 | no |
| 1c location tag replaces any venue when an @mention spells it | 0/0 | 3/0 | yes |
| 2a 📍 name fills an empty venue | 4/0 | 3/1 | yes |
| 2b 📍 name replaces a weak-slot venue | 2/4 | 7/1 | no |
| 2c "at/from/tried @handle" fills an empty venue | 0/1 | 1/0 | no |
| 2d ad cue + no 📍 + no location tag → no venue | 0/0 | 4/0 | yes |
| 2e tidy the name: split the handle, drop "official", drop the branch/city suffix | 0/1 | 17/4 | no (gold train) |

**Kept rules on dev: 71/106 (67.0%) vs 69/106. Fixed 2, broke 0, McNemar p = 0.50, not
significant.** With Nick's tolerance (one name contains the other): dev 73.6% → 75.5%.

The location tag is mostly *not* a missing-venue fix. When the parser leaves the venue
empty, the tag is usually a city, an event or a landmark (1a, 1b). The parser's existing
gate (`_tag_names_a_venue`) is already right to be strict.

2e is promising on silver (+13 net) but broke one gold train reel. A first version of it
used city words copied from dev misses; that version scored +6 on dev and was discarded as
tuning on dev. The version above takes its city words from train labels only.

## 4. Where the remaining dev misses are (parser + kept rules, 35 misses)

| Bucket | Reels |
|---|---|
| Venue not in the caption, @mentions or location tag | 19 |
| Venue visible, parser picked other text (often a branch or city suffix) | 7 |
| Venue visible, parser found nothing | 5 |
| Not a place, parser invented a venue | 4 |

Several "not in caption" misses are near-misses the strict scorer rejects ("Tacolandofficial"
vs "Taco Land", "Churritoloco Southgate" vs "Churrito Loco"). That is what 2e targets.

## 5. Bug for Track A: transcription silently returns nothing

With faster-whisper 1.2.1 and PyAV 19 installed, `faster_whisper.decode_audio` calls
`av.open(..., metadata_errors=...)`, which PyAV removed. `pipeline/transcriber.py` catches
the error and returns `None`, so **every transcript comes back empty, and nothing reports
it**. The documented pin (`faster-whisper==1.0.3` in `requirements-ingestion.txt`) predates
this. Two possible fixes: pin `av<15` next to faster-whisper, or decode with ffmpeg and pass
Whisper a 16 kHz array, which is what `experiments/burner_study/pilot_av.py` does. Either fix
should get a test that fails when the transcript is silently empty.

## 6. Fine-tuned transformer span tagger

DistilBERT-multilingual, fine-tuned for 4 epochs on the same 906 train examples as the CRF.
It took 631 seconds on the CPU. Model saved to `data/models/span_tagger_v0` (not in git).
The first scoring had a decoding bug: it kept only the first word piece of a name, giving
"Tien" for "Tiendita". The bug showed up on train examples. After fixing the decoding, the
model was re-scored without retraining (`score_tagger.py`).

| System | Dev same place | Dev tolerant |
|---|---|---|
| Parser | 69/106 (65.1%) | 78/106 |
| CRF | 28/106 (26.4%) | not measured |
| Transformer | 44/106 (41.5%) | 64/106 |
| Parser, using the transformer only where the parser is empty | 69/106 | 80/106 |

The transformer beats the CRF but not the parser, and adds little as a gap filler. With
about 670 venue spans, most of them silver, it hasn't learned what the parser's rules encode.

## 7. Audio / on-screen text pilot (20 low-text burner reels)

The reels were fetched through the burner at 15, then a 10-minute wait, then 5. Speech was
transcribed with Whisper "base" on the CPU, decoded through ffmpeg because of the bug in
section 5. On-screen text was read by OCR from the cover image.

- Of the 6 reels where the caption never named the venue, 4 got a name. Speech gave
  "Leo's Mexican Grill" and a banh mi shop. On-screen text gave a Northridge matcha spot
  and a Costa Mesa bakery.
- Of the 14 uncertain venues, about 6 were confirmed by speech or on-screen text (for
  example Little Toni's, Krave It, Calico Cidery, Halal Munchies).
- About 6 of 20 had nothing useful: music only, or a song's lyrics transcribed.
- Quality problems: Whisper "base" mishears names ("Crabit" for Krave It, "Ford Shown" for
  Fogo de Chão). The OCR output drops spaces ("canconfirmthatthenew.rok matcha").

**Speech or on-screen text helped on about 10–12 of 20.** That passes the bar set before the
pilot (about half), so it's worth capturing at scale.

## What this points to

1. Capture more of the reel. Audio and on-screen text are the only way past the 73.6% ceiling.
2. Name cleanup (2e) needs more gold train reels to decide. It is the one rule with a large
   silver signal.
3. Fix the dev label DXR9yLHESKQ.
