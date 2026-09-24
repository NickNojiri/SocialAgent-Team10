# Accessibility checklist (Track C #37)

Target: WCAG 2.1 AA for SpotBot's three web pages, and the same intent for the
Discord side where Discord lets us control it. Last run **2026-09-24**.

Each row says how it was checked: **auto** = a test in the offline suite, so it
can't quietly regress; **browser** = inspected in a real browser's accessibility
tree; **person** = still needs a human with a screen reader.

## Web pages — catalog `/`, share `/share`, ops `/dash`

| # | Check | Result | How |
|---|---|---|---|
| 1 | Text contrast ≥ 4.5:1 for every text/background pair the pages use | ✅ all 29 pairs pass; the tightest is the red error text on cards (5.0:1), then muted grey (5.4:1) | auto — `test_accessibility.py::test_text_meets_wcag_aa_contrast`, computed from each page's own color tokens |
| 2 | Every image has alt text | ✅ share-page thumbnails read "Photo from the post about ‹venue›" (was `alt=""`) | auto + browser |
| 3 | Every button has a name a screen reader can say | ✅ ▲/▼ read "Vote up/down ‹venue›"; 🗑 reads "Remove ‹venue›" | auto + browser |
| 4 | Every form field has a label, not just a placeholder | ✅ all four fields on the catalog page | auto + browser |
| 5 | Color is never the only signal | ✅ dash status dots are repeated in words ("Ollama LLM — down", "job store: in-memory — needs a look") | auto + browser |
| 6 | Decorative emoji hidden from screen readers | ✅ headings, category badges, dates, 👍, 🗑 | auto |
| 7 | Links that open a new tab say so, and can't reach back (`rel="noopener noreferrer"`) | ✅ source, map and footer links | auto |
| 8 | Page language, title, `<main>` landmark | ✅ all three | auto |
| 9 | Results announced when they change | ✅ capture and add-spot status lines are `role="status"` | auto (markup) |
| 10 | Table headers marked as column headers | ✅ dash tables use `scope="col"`; the bar-chart column is hidden (the count is in the next cell) | browser |
| 11 | Keyboard: everything reachable and visibly focused | ✅ native buttons/links/fields only, no `outline:none` anywhere | markup review |
| 12 | Screen-reader walkthrough (NVDA on Windows) of all three pages | ⬜ not done | **person** — ~10 minutes; record what's read aloud |

Known limit: `/dash` rewrites itself every 5 seconds. It isn't a live region, so a
screen reader isn't interrupted, but its focus can reset. It's an operator page on
localhost; left as is.

## Discord (bot)

| # | Check | Result | How |
|---|---|---|---|
| 13 | Every button has a text label, not only an emoji | ✅ `/browse` ◀ ▶ now "Previous"/"Next"; all other buttons already had words | auto — `app/test_browse.py` |
| 14 | Card images have alt text | ⚠️ **not possible** — Discord embeds have no alt-text field for images. The card's title and text already name the venue and category, so the thumbnail adds nothing a reader misses | platform limit |
| 15 | Meaning isn't carried by emoji alone | ✅ category emoji sit next to the venue name; the footer names the category; survey buttons are digits with the scale in words | review |
| 16 | Contrast inside Discord | n/a — Discord's own themes control it | — |
| 17 | Discord with a screen reader: paste a reel, vote, `/browse`, `/survey` | ⬜ not done | **person** |

## To finish #37

Rows 12 and 17 need a person. Then this file is the checklist for the final report.
