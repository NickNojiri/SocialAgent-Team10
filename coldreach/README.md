# ColdReach

A local-first cold-email agent for academic outreach. It drafts short emails to
professors with a small local model, tracks every contact in SQLite, watches your
inbox for replies, classifies them, and answers a "yes" with a real calendar
invite.

Nothing leaves your machine except the email itself. No SaaS, no cloud LLM API,
no Google Cloud project, no scheduling links, no GPU.

## What it costs to run

| Resource | Cost |
| --- | --- |
| LLM | `qwen2.5:3b` via Ollama, ~2 GB RAM while generating, unloaded between runs |
| Database | one SQLite file |
| Email | your existing mailbox over SMTP/IMAP with an app password |
| Scheduling | `.ics` files generated locally |
| Dependencies | `ollama`, `icalendar`. Everything else is the standard library |

`CR_OLLAMA_KEEP_ALIVE=0` (the default) tells Ollama to unload the model the
moment each call returns, so a daily cron run leaves nothing resident. The whole
agent idles at zero CPU and zero RAM between runs.

## Install

```bash
git clone <this-repo> coldreach && cd coldreach
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# The local model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b

cp .env.example .env && $EDITOR .env
python cli.py doctor          # verifies config, Ollama, SMTP, IMAP
```

`doctor` prints a line per subsystem and exits non-zero if anything is broken.
Run it first; it is much easier to debug than a silent cron job.

### Environment variables

Everything lives in `.env` (read automatically, never committed). The ones you
must set:

| Variable | Meaning |
| --- | --- |
| `CR_FULL_NAME` | Your name, used in the signature and the invite |
| `CR_EMAIL` | Your address, used as From and as the IMAP/SMTP login |
| `CR_EMAIL_PASSWORD` | **App password**, not your account password |
| `CR_ROLE_BLURB` | One phrase describing you, fed to the model |
| `CR_TIMEZONE` | IANA name, e.g. `America/Los_Angeles`, used for meeting times |

Gmail app password: enable 2FA, then <https://myaccount.google.com/apppasswords>.
Gmail also needs IMAP switched on in Settings → Forwarding and POP/IMAP.
Defaults target Gmail; `.env.example` lists the Outlook hosts.

The rest (`CR_OLLAMA_MODEL`, `CR_MEETING_HOURS`, `CR_MAX_SENDS_PER_RUN`,
`CR_LEAD_TIME_HOURS`, `CR_SEND_DELAY_SECONDS`, `CR_NET_TIMEOUT`, …) have working
defaults and are documented inline in `.env.example`.

Smaller models work too. `qwen3:1.7b` and `llama3.2:1b` both run; their drafts
are noticeably blander, and `_sanitise_body` rejects output under 20 words so a
bad generation leaves the row `Pending` for the next run rather than sending
something broken.

## Use

```bash
python cli.py init                      # create professors.db
python cli.py import contacts.csv       # Name,Email,Research_Topic[,Institution]
python cli.py draft                     # Pending  -> Drafted     (Phase 1)
python cli.py review                    # read every draft yourself
python cli.py approve --all             # Drafted  -> Approved
python cli.py send                      # Approved -> Sent
python cli.py poll                      # inbox -> classify -> invite (Phase 2)
python cli.py status                    # pipeline counts, upcoming meetings
```

`approve --ids 3 7 9` approves individual rows. `send --dry-run` and
`poll --dry-run` print what would be sent without touching the network.

### The status flow

```
Pending ──draft──> Drafted ──approve──> Approved ──send──> Sent
                                                             │
                                                          (reply)
                                                             │
              ┌──────────────┬───────────────┬───────────────┤
           Positive       Question         Soft No        Hard No
              │               │               │               │
        ics invite sent    Replied         Soft No     Hard No + suppressed
              │
          Scheduled
```

`Failed` catches SMTP errors so a broken send never silently disappears.

### Why sending is a separate manual step

Phase 1 stops at `Drafted` on purpose. A 3B model will occasionally produce
something bland, wrong, or oddly phrased, and a cold email to a professor is not
a thing you get to retract. `review` prints each draft in full; `approve` is the
gate. Cron never sends cold email — it only drafts and listens.

Deliverability guards, all configurable: at most `CR_MAX_SENDS_PER_RUN` per
invocation, `CR_SEND_DELAY_SECONDS` between messages, a `List-Unsubscribe`
header, and a plain opt-out line in every email. A "Hard No" reply adds the
address to a suppression table that `draft_pending` and `send_approved` both
check, so a refusal is permanent.

## Cron

Run once a day on weekday mornings. This drafts new contacts and processes
replies; it does not send cold email.

```cron
30 8 * * 1-5 cd /home/you/coldreach && .venv/bin/python cli.py daily >> cron.log 2>&1
```

For every day including weekends:

```cron
30 8 * * * cd /home/you/coldreach && .venv/bin/python cli.py daily >> cron.log 2>&1
```

Install with `crontab -e`. Cron runs with a bare environment, which is exactly
why config lives in `.env` next to the code rather than in your shell profile —
the `cd` is what makes it load.

Replies feel more responsive if you poll more often than you draft. Optional:

```cron
0 * * * * cd /home/you/coldreach && .venv/bin/python cli.py poll >> cron.log 2>&1
```

Each run is cheap: an inbox with no new replies costs one IMAP fetch and zero
model calls, because the model is only invoked for messages that match a
contacted professor and have not been seen before.

## How Phase 2 decides

1. `fetch_recent` pulls the last 30 days with `BODY.PEEK[]`, so your unread
   flags are never touched.
2. Anything whose `From` does not match a professor with a `sent_at` is ignored.
3. `Message-ID` is recorded in the `inbound` table, so re-running `poll` never
   double-processes a reply or sends a second invite.
4. Auto-responders are caught by header (`Auto-Submitted`, `X-Autoreply`,
   `Precedence`) before the model sees them, and classified `OOO` without
   changing the professor's status — so the real reply still lands later.
5. Quoted history is stripped, and the remaining text goes to the model in JSON
   mode. If the model is down or returns junk, `heuristic_intent` classifies on
   keywords instead. Classification never hard-fails.
6. On `Positive`, `next_free_slot` picks the first weekday slot at least
   `CR_LEAD_TIME_HOURS` out, in `CR_MEETING_HOURS`, that no existing meeting
   holds. The invite is sent as `text/plain` + `text/calendar; method=REQUEST`
   plus an `.ics` attachment, threaded with `In-Reply-To`, which is what makes
   Gmail and Outlook render it as an accept/decline invite.

## Tests

```bash
pip install pytest && python -m pytest test_coldreach.py -q
```

34 tests, all offline: no network, no Ollama, no mail server. The model and IMAP
are stubbed, so the draft, classify, schedule, and suppression paths are all
exercised in isolation.

## Files

| File | Role |
| --- | --- |
| `coldreach.py` | The engine: config, DB, Ollama, SMTP/IMAP, ICS, both phases |
| `cli.py` | Command-line front end |
| `test_coldreach.py` | Offline test suite |
| `.env.example` | Every setting, documented |
| `contacts.sample.csv` | CSV column format |

## A note on using this

Cold-emailing professors is normal and often welcome. Sending volume is not.
This tool caps sends, spaces them out, honours refusals permanently, and makes
you read every draft before it goes out, because a mailbox full of obviously
generated mail is worse for you than sending nothing. Keep the list small and
the topics real.
