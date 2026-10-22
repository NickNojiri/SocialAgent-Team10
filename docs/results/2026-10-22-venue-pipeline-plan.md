# Venue extraction, version 2: what to use at each step (plan, 2026-10-22)

Status: **proposal, nothing built into `src/` yet.** It's grounded in the burner-reel study
(`2026-10-22-burner-study.md`), the existing ranker (`experiments/ranker.py`), and published work
on finding place names in social posts.

## What the evidence says

**Our data**
- The parser gets 65.1% of dev venues (same place). Only 73.6% of dev reels name the venue in
  the caption or @mentions at all, so caption-only methods are close to their ceiling.
- `experiments/ranker.py` found the right name *among its candidates* for 85.8% of dev reels,
  but picking among them with caption features ties the parser. Its notes end by saying the
  remaining misses "need signals the caption text doesn't carry."
- More labels through the alias table: about 0. Caption grammar rules: +2 reels.
  Places lookup (Overture): +1 to +2 reels. A fine-tuned transformer on ~900 mostly-silver
  examples: 41.5%.
- Audio + on-screen text pilot: speech or screen text named or confirmed the venue on about
  10–12 of 20 low-text reels.

**Published work** (sources at the end)
- The standard design for places in social posts is **recognize → generate candidates → link to
  a place database → rank, with a "none" option**. Examples: Li & Sun's POI inventory + CRF
  tagger, DLocRL's recognizer + linker, ParsEL's candidate ranking.
- Gazetteer matching alone is noisy (0.35 F1 in skweak's comparison). It works as one signal
  among several, not as the decider.
- When labels are scarce, **weak supervision** works: many noisy labeling rules, combined by a
  model that learns how much to trust each one, then a sequence tagger trained on the result
  (Lison et al., ACL 2020; skweak). That beat an out-of-domain NER model by about 7 F1.
- LLMs do best on entity linking as a **multiple-choice judge over a short candidate list with a
  "none of these" option** (ChatEL, OneNet, LELA), not by generating names freely. That matches
  this repo's finding that a free-form local LLM made things worse.

## The pipeline, step by step

| Step | What it decides | What to use | Why |
|---|---|---|---|
| 0. Inputs | everything the reel offers | caption, location tag **with IG's lat/lng**, @mentions, poster handle + display name + **IG account type and business category**, transcript (Whisper "small", fixed decoder), on-screen text (cover + a few frames) | The 26% ceiling gap is in speech, screen text and the account. IG already says whether an account is a business and what kind (`account_type`, `business_category_name`): a direct fact, not a guess. |
| 1. Post type | one place / list / venue later (promoter) / not a place / can't tell | small multi-class classifier (logistic regression) over cue features: 📍 count, numbered items, "comment X / link in bio / text 555", tickets/RSVP, recipe words, poster role | Decides first whether there is a venue to find at all. "Venue later" keeps promoter posts from being thrown away, and "can't tell" stays out of scoring. |
| 2. Poster role | venue's own account / reviewer or creator / promoter / brand | IG account type + business category; handle or display name matches a real venue in the reel's city (places lookup); "we/our/us/join us" vs "I/my/follow me"; display name is a person's first name (public name list); blogger-handle words (eats, foodie, bites, explore) | You flagged this, and it's the biggest missing signal. **Labels are free:** in every gold row, the poster is the venue exactly when the gold venue matches the handle or display name. |
| 3. Candidates (high recall) | every name the reel could be about | the ranker's existing candidate list, plus: names in the transcript and screen text, places-lookup names found in any of the text, the poster's name **only if step 2 says venue account**, the location tag | Target: the right name is a candidate for well over 85.8% of reels, measured on its own. |
| 4. Link / verify | is this candidate a real venue, and which one | Overture places (now SoCal + NYC; add regions as the corpus needs). With IG coordinates: nearest venues within about 150 m. Without: venues in a city the reel mentions. Match on full name or prefix-plus-location (the rule tested in the study). Multi-label categories come from the place's Overture category path. | Coordinates make linking nearly exact for about 40% of reels. Linking also fixes near-miss names ("Tacolandofficial" → "Taco Land") and gives categories. |
| 5. Rank and pick | which candidate, or none | extend `experiments/ranker.py` with step 1–4 features: post type, poster role, linked yes/no + distance + match strength, source (caption/speech/screen/tag), how many sources agree | Same softmax-with-a-none-option model. New features are what it was missing. |
| 6. Judge (only when unsure) | close calls | local Ollama model answering **multiple choice** over the top 3 verified candidates + "none" | Published work shows this format avoids made-up names. It only runs when the ranker's margin is small, so it stays cheap. |
| 7. Category (multi-label) | what kind of spot | the linked place's Overture categories when linked; otherwise the keyword categorizer; keep all labels, pick one only for display | A brewery can be nightlife and food at once. |

## Fine-tuning plan

1. **Don't let the transformer pick the venue yet.** With ~900 mostly-silver examples it reaches
   41.5%. Use it as a candidate source and a ranker feature instead.
2. **Build training data with weak supervision.** Write every rule (📍, at/from @, location tag,
   places lookup, poster role, transcript cues) as a labeling function. Combine them with a
   label model that learns each rule's accuracy (skweak's HMM, or majority vote to start). Run it
   over all reels: the 853 burner reels now, and the data plan's 8,000-reel silver set later.
3. **Fine-tune a social-media multilingual encoder** (Twitter-XLM-R base) on caption + transcript
   + screen text joined with separators, trained on that weakly labeled data. Score its spans
   only through steps 4–5.
4. **The ranker is where few labels go furthest:** hundreds of gold reels × their candidates.
   Every gold label you confirm lands there.

## How each step gets tested (same rules as today)

- Measure each step on its own. Candidate recall (is the answer on the list?) is separate from
  ranking accuracy (did we pick it?), so we always know which step to fix.
- Decide on train + silver, confirm on the same dev reels, McNemar paired test, report the
  tolerant score too. "Can't tell" reels are left out, and the count is always reported.
  Test is read once, at the end.
- Dev is 106 reels (±9 points). Gold you confirm that falls in dev grows it toward the data
  plan's 500, and that's what makes 3–5 point gains provable.

## Order of work (accuracy per effort)

1. **Candidate recall audit by source** (caption / tag / speech / screen / lookup / poster).
   Cheap, and shows where the ceiling really is. Uses the 142 transcribed reels.
2. **Poster role** (step 2): free labels from gold rows. IG account type costs one request per
   new poster, cached; that's about 70 minutes of burner pacing for the ~100 dev posters.
3. **Coordinate linking** (step 4) for reels with a location tag.
4. **Ranker v3** with steps 1–4 features. The first realistic chance at a significant dev gain.
5. Transcript + screen text in the candidates, at scale (Track A turns capture on for new reels).
6. Weak-supervision silver + Twitter-XLM-R tagger.
7. LLM multiple-choice judge for close calls.

## Sources

- Li & Sun; Xu et al., DLocRL (WWW 2019): https://arxiv.org/pdf/1901.07005
- Hu et al., Location reference recognition from texts, a survey (ACM CSUR 2023): https://dl.acm.org/doi/full/10.1145/3625819
- POI extraction from social media (METU thesis): https://open.metu.edu.tr/bitstream/handle/11511/26477/index.pdf
- LNEx, gazetteer-based location name extraction (COLING 2018): https://aclanthology.org/C18-1169.pdf
- ParsEL, unsupervised entity linking in social media text: https://arxiv.org/pdf/2004.10816
- Lison et al., NER without labelled data, a weak supervision approach (ACL 2020): https://virtual.acl2020.org/paper_main.139.html
- skweak toolkit review: https://liner.com/review/skweak-weak-supervision-made-easy-for-nlp
- OneNet (EMNLP 2024): https://arxiv.org/html/2410.07549v1
- ChatEL review: https://www.themoonlight.io/tw/review/chatel-entity-linking-with-chatbots
- LELA (2026): https://arxiv.org/html/2601.05192v1
- Adaptive routing for LLM entity linking: https://arxiv.org/html/2510.20098v2
- Toponym resolution with lightweight open LLMs (IJGIS): https://elib.dlr.de/208870/1/Toponym%20resolution%20leveraging%20lightweight%20and%20open-source%20large%20language%20models%20and%20geoknowledge.pdf
- Instagram account types (Business / Creator / Personal): https://docs.spotlightwp.com/article/902-how-to-identify-if-your-instagram-account-is-business-creator-or-personal
