# Handoff to Track C: what changed in the bot (`app/`)

From Nick (Platform), 2026-09-24. This is the "You → C" Phase 1 handoff in
`docs/tracks/TRACK-N-PLATFORM.md`. Every bot change since #25 is listed below, oldest
first, with the commit to read and the test file that covers it. Each `app/` change
landed in its own commit, apart from the platform half, so `git show <sha>` shows you
just the bot side.

Tests for all of it: `python -m pytest app -q`.

## Capture: how a pasted reel becomes a card

| Commit | Feature | What a user sees now | Tests |
|---|---|---|---|
| `bbf92f0c` | #25 | Pasting the same reel twice, or pressing Retry, follows the capture already running: one job, one card. After 180 s the status line adds "Still working – N min so far" and refreshes every minute. | `app/test_capture.py` |
| `cc0b4064` | #26 | While a timed-out link waits to be retried, the status says it's trying again instead of "Reading that reel…". | `app/test_capture.py` |
| `6375587b`, `92f7fd36` | #27 | Captures go through the async job queue **by default** (`INGEST_ASYNC=1`). A paste never blocks the bot. A full queue gets a real message, not a hang. | `app/test_capture.py` |
| `ad85f531` | #27 | If the catalog restarts and loses a capture, the user sees plain words ("…tap Retry"). The operator detail goes to the log only. | `app/test_capture.py` |
| `85b28212` | #27 | The bot keeps polling through up to 60 s of catalog outage (`INGEST_POLL_ERROR_S`) before giving up. | `app/test_capture.py` |
| `53b27c8c`, `e92c4856` | #28 | Capture requests are signed for the user who pasted. A rate-limited user is told when to try again ("try again in 10 minutes"), on both capture paths. | `app/test_capture.py` |
| `caa3400d` | #18 | Every paste is timed from seeing it to the card (or failure) being up, and reported to `/api/time-to-card` afterwards. The report never slows the user down. | `app/test_capture.py` |
| `449ced9e` | #19 | Every failure gets one plain sentence and a next step, per failure class. Raw errors go to the log, never the channel. A paste where some links worked names the ones that didn't. | `app/test_messages.py` |

## New and changed commands

| Commit | Feature | Command | New file | Tests |
|---|---|---|---|---|
| `9f596086` | #8 | `/setup`: Manage Server gets a private 3-step form (reels channel, home city, Save). Once a reels channel is set, only it (and its threads) captures. A one-time welcome message on joining a server. | `app/setup_wizard.py` | `app/test_setup.py` |
| `5fa45e21` | #21 | `/privacy`: what's kept and where, plus a two-step delete (type the server's name) for Manage Server or your own DM stash. | `app/privacy.py` | `app/test_privacy.py` |
| `efdaf1b0` | #35 | `/browse` gains area (autocomplete) and "has a date / no date yet" filters, combined with category. | — | `app/test_browse.py` |
| `b8e00f59` | #20 | `/feedback bug|idea` form; `/survey [participant]` runs the 10 SUS statements one tap each. | `app/feedback.py` | `app/test_survey.py` |
| `e9010a8c` | #37 | `/browse` pager buttons say Previous/Next (emoji-only buttons read badly on screen readers). | — | `app/test_browse.py` |
| `06f0f904` | #36 | Every card gets a map link, and "N mi from <home city>" when the spot has coordinates and `/setup` set a city. | — | `app/test_cards.py` |
| `7d204e02` | #11 | `BOT_CONFIG_FILE` lets `channels.json` live somewhere else (staging). Default unchanged. | — | `app/test_bot.py` |

## Bot settings (environment)

New or changed since #25: `INGEST_ASYNC` (now `1` by default), `INGEST_POLL_ERROR_S`
(60), `INGEST_SLOW_AFTER_S` (180), `BOT_CONFIG_FILE` (`channels.json`). Unchanged but
related: `INGEST_POLL_S` (3), `INGEST_WAIT_S` (900).

## Per-server setting keys (`/setup`, stored by the admin app)

From `src/ingestion/serving/guild_settings.py`. One JSON file per server under
`data/guild_settings/`:

| Key | Meaning | Default |
|---|---|---|
| `drop_channel_id` | The one channel to watch for reels | `None` = every channel |
| `home_city` | The group's home city, as typed (max 80 chars) | `""` |
| `home_lat`, `home_lng` | That city's coordinates, looked up once on save | `None` |
| `updated_at` | Unix seconds of the last save | `0` = never configured |

No user ids, message text or tokens are stored.

## Still yours for Phase 1

These were deliberately left for a person:
- Run `/setup` on a **fresh** Discord server with a stopwatch, and show the first card
  in under a minute. Save a recording or a written timing.
- Two accessibility checks in `docs/ACCESSIBILITY.md` need someone with a screen reader.
- #22 (usability study) is Phase 3.
