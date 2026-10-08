# Labeling guide — `fixtures/labels.jsonl`

One page so that every labeler writes the same answer for the same reel, and the
scorer and the labels agree on what "right" means. Track B owns this file; the
tools that enforce it are `scripts/label_audit.py` and `scripts/kappa_batch.py`.

## §1 What a label is

Each row has `input` (what the extractor saw), `gold` (the right answer, from the
reel itself), `verdict` (was the stored prediction right/wrong/missing), and
`labeler` (who wrote `gold`). Label from the reel — video, audio, on-screen text,
caption — not from the bot's card.

## §2 Venue (`gold.venue`)

1. **The business's own name, as on its sign or Google Maps.** `BJ's Restaurants`,
   `Tahoe Bagel Company`, `Fleurs et Sel`. Keep words that are part of the name
   (`Company`, `Bakery Cafe`, `Soft Serve`).
2. **No location qualifier.** Drop a city, neighbourhood, branch or mall:
   `Phin Coffee`, not `Phin Coffee Torrance`; `Concerto`, not `Concerto • Koreatown`.
3. **One stall inside a complex → the stall.** A corn dog from Blue Ribbon at
   Downtown Disney is `Blue Ribbon Corn Dogs`, with the complex's city in
   `gold.city`. A reel touring **several** spots in one complex has no single
   venue: `venue: null`. (Older rows label both cases `Disneyland`; the audit lists
   them under `place` so they can be brought in line.)
4. **Several venues (rankings, roundups) or no venue (recipes, products, ads,
   memes) → `venue: null`** and see §4.
5. **A city, region or complex is never the venue.** `Mountain View: Matcha Mori`
   is the venue `Matcha Mori`. The audit flags gold venues that are places.
6. **Spell it the same way every time.** The audit flags one place written two ways
   (`yamas teriyaki` / `Yamas Teriyaki House`).

## §3 How the venue is scored

Two numbers are reported, always side by side (`python -m src.ingestion.eval`):

- **venue exact** — prediction and gold are equal after lowercasing and turning
  punctuation into spaces. Unchanged since 2026-09; the CI floors use it.
- **venue same place** — the prediction names the same place. It counts as a match
  only when one of these holds, and nothing else:
  1. the names are identical ignoring case, spaces and punctuation
     (`Shugarshack` = `Shugar Shack`, `Bjs Restaurants` = `BJ's Restaurants`);
  2. one name is the other plus only a **location** tail (a known city/area/region,
     a state or market abbreviation: `Phin Coffee` = `Phin Coffee Torrance`);
  3. one name is the other plus only a **business-type** tail (`cafe`, `bakery`,
     `company`, `house`, `bar`, `kitchen`…: `Tahoe Bagel` = `Tahoe Bagel Company`),
     and the shared part contains a real name word (`Coffee` ≠ `Coffee Company`).

These rules are fixed here, are applied identically to every run being compared,
and live in one function (`eval.venue_same_place`). Never add per-row aliases to the
scorer to make a row pass; fix the extractor or the label.

## §4 Catalog inclusion and category

- `gold.in_catalog` is **true** when the reel is about one real place a group could
  go to, **false** for promos, products, recipes, rankings and memes. A row with
  `in_catalog: false` has `venue: null`; a row with `in_catalog: true` has a venue
  or at least a city.
- `gold.category` is **what the reel is about**, not the venue's name: a S'mores
  Pizookie at BJ's Restaurants is `cafe_dessert`; a full meal at Cafe Landwer is
  `food_drink`. Drinks and bars are `nightlife`; pop-ups, markets and festivals are
  `market_popup`. Use `other` only when it is none of the categories.

### Cuisine and dish (`gold.cuisine`, `gold.dish`)

Optional, food posts only; leave both `[]` when the reel doesn't show or say it.
Both are **lists** (a Korean-Mexican taco spot is `["korean", "mexican"]`), and use
only these words, so two labelers write the same thing:

- `cuisine`: `american`, `chinese`, `japanese`, `korean`, `vietnamese`, `thai`,
  `filipino`, `indian`, `mexican`, `latin_american`, `italian`, `french`,
  `mediterranean`, `middle_eastern`, `other`. Fusion lists each side.
- `dish`: `noodles`, `rice`, `sushi`, `bbq_grill`, `burger_sandwich`, `pizza`,
  `tacos`, `fried_chicken`, `seafood`, `bread`, `pastry`, `cake`, `ice_cream`,
  `coffee`, `tea_boba`, `drinks_bar`, `brunch`, `other`. Name what the reel
  features, at most three.

A word not on a list is `other` plus a note; add it to the list here only when it
keeps coming up.

## §5 Inputs and re-capture

A label is only fair if the input holds what the labeler used. When the venue is
only on screen or only in the comments, the extractor cannot see it until the row is
re-captured with OCR / comments. The audit lists every gold venue that appears
nowhere in the stored input; set `needs_recapture: true` on those rows, and score
them separately rather than tuning rules toward them.

## §6 Labelers and agreement

- Put your name in `labeler` on every row you write. Rows marked `unknown` cannot be
  used for agreement.
- Every ~100 new rows, a second person labels a blind slice:
  `python scripts/kappa_batch.py export --n 40` writes `data/kappa_batch.csv` (no
  gold, no prediction). They fill it from the reels in Google Sheets, then
  `python scripts/kappa_batch.py import data/kappa_batch.csv --labeler <name>` adds
  `second_label` to those rows. `python -m src.ingestion.eval --offline --full-report`
  prints Cohen's kappa per field. Disagreements are resolved by these rules, not by
  the louder labeler, and a rule that keeps causing disagreement is rewritten here.

## §7 After any labeling session

```bash
python scripts/label_audit.py                 # fix what it flags
python scripts/build_aliases.py               # rebuild both alias tables
python -m src.ingestion.eval --offline --split train
```
Then raise the floors in `test_extraction_labels.py` to the new train numbers
(floors only go up). Check `--split test` only for the number you report.
