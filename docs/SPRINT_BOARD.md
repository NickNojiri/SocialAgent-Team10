# Sprint board — our agile tool in Discord

Our agile board is a Discord **forum channel, `#sprint-board`**, in the team server. A
Claude routine checks it on Monday, Wednesday and Friday mornings. The professor approved this tool for
CECS 491A. The code is `scripts/sprint_board.py` (standard library only), and its tests are in
`test_sprint_board.py`. What's left to finish it, in order: [`SPRINT_BOARD_PLAN.md`](SPRINT_BOARD_PLAN.md).

```
 docs/tracks/TRACK-*.md ──seed──▶  #sprint-board (forum)  ◀── you: reply, change tags
   (the plan, per phase)           one post per feature
                                   tags: status · track · phase
                                          │
              Mon/Wed/Fri 8:51 PT         ▼
              Claude routine ──── report --json ──▶ reads the board + your replies
                                  standup        ──▶ opens "Standup — Wed Oct 21"
                                  post --into    ──▶ a short check-in note in it
                       (Mondays)  post --title   ──▶ "Sprint digest — week of Oct 19"
```

## How the team uses it

- **Start with the pinned post**, `Setup — how to use the sprint board`. It holds this section
  in short, how to run the script (below), and a step-by-step "How it works".
- **One post = one feature** from the track plans, e.g. `#27 Non-blocking capture with live
  progress ★ (80 pts)`. The opening message lists the plan's checklist and links the plan file.
- **Tags carry the state.** Every post has three tags:
  - **status:** 📋 Backlog → 🔧 Doing → 👀 Review → ✅ Done, or 🚧 Blocked;
  - **track:** A · Capture, B · Accuracy, C · Experience, D · Security, N · Platform;
  - **phase:** Phase 1–4.

  To change a status, open the post, click the tags, swap the status tag, and keep the other two.
- **Progress goes in replies** on the feature's post: what changed, the PR link, what's next.
  If you're blocked, say what you're waiting on and who can unblock it.
- **Standup** (Mon/Wed/Fri, async): the routine opens a `Standup — <day>` post. Reply with
  three lines: **Did / Doing / Blocked**.
- **Sprint planning:** the first meeting of each phase. Each owner moves 2–4 of their posts
  from Backlog to Doing. **Sprint review:** each person demos what moved to Review or Done.
- **Definition of done:** the same as in `docs/CAPSTONE_PLAN.md`: merged to main, one non-author
  approval, tests, CI green, demoed. A post is ✅ Done only then. Until then it is 👀 Review.

Nobody gets pinged. The routine writes names, never @mentions, and the script sends
`allowed_mentions` with nothing allowed (`docs/THREAT_MODEL.md` T1).

## The commands

```bash
python scripts/sprint_board.py setup                  # create #sprint-board and its 16 tags (safe to re-run)
python scripts/sprint_board.py seed --phase 2         # dry run: which posts it would make
python scripts/sprint_board.py seed --phase 2 --apply # post them (features already there are skipped)
python scripts/sprint_board.py report                 # the board as Discord markdown
python scripts/sprint_board.py report --json --since 7  # + people's replies from the last 7 days
python scripts/sprint_board.py standup                # open today's standup post
python scripts/sprint_board.py post --title "Sprint digest — week of Oct 19" --file digest.md
python scripts/sprint_board.py post --into <post id> --file note.md
python scripts/sprint_board.py move <post id> Review  # swap only the status tag
```

The feature list comes from the `### Phase N` sections of `docs/tracks/TRACK-*.md`, in the
form `**#27 Title (80)**` followed by `- [ ]` checkboxes. A post starts as Backlog if no box
is ticked, Doing if some are ticked, and Done if all are. After that, the tags on the board are the
truth. The phase dates come from the table in `docs/tracks/README.md`.

How `report` counts: a post's points come from `(80 pts)` at the end of its title, and a post
counts toward a phase only if it carries that phase's tag. A post with no status tag counts as
Backlog. So a feature post missing its tags drops out of the phase totals; tag it by hand.

## Setting it up by hand (no bot, about 20 minutes)

Use this when the bot isn't set up yet. The board looks the same and the routine takes it
over later, because `setup` keeps tags that already exist and `seed` skips any feature
whose `#number` is already posted.

1. **Make the forum.** Forum channels need a Community server (Server Settings → Enable
   Community). Click **+** → **Forum** → name it `sprint-board`. In its settings → **Tags**,
   add these tags:
   - Status: `Backlog`, `Doing`, `Review`, `Blocked`, `Done`
   - Track: `A · Capture`, `B · Accuracy`, `C · Experience`, `D · Security`, `N · Platform`
   - Other: `Phase 2`, `Standup`

   Case and separators don't matter (`doing` and `A - Capture` work too).
2. **Make one post per feature.** Title it `#<number> <title> (<points> pts)`, the same
   title as in the track plan. Add a status tag, a track tag and `Phase 2`. The first message
   names the owner and says what is done and what is left. Phase 2, as on the board on
   Oct 20 (580 pts):

   | Title | Tags | Owner |
   |---|---|---|
   | #2 Authenticated capture + fallback chain (100 pts) | Doing · A · Capture | Blessie |
   | #16 Learned category classifier (100 pts) | Backlog · B · Accuracy | Valeria |
   | #17 Evaluation report generator (30 pts) | Backlog · B · Accuracy | Valeria |
   | #18 Time-to-card measurement (30 pts) | Review · N · Platform | Nick |
   | #20 In-Discord feedback + survey ★ (40 pts) | Done · C · Experience | Nick |
   | #36 Distance + map on spot cards (40 pts) | Done · C · Experience | Nick |
   | #27 Non-blocking capture with live progress (80 pts) | Doing · N · Platform | Nick |
   | #10 Secrets management & signing-key rotation (60 pts) | Doing · N · Platform | Nick |
   | #30 Authentication & session hardening (100 pts) | Doing · D · Security | Kimberly |

   Course deliverables, such as the Design Document, get their own post with a status tag
   and `Phase 2`.
3. **Open a standup.** Make a post titled `Standup — <Day Mon D>`, for example
   `Standup — Sat Oct 17`, tag it `Standup`, and write: "Reply with three lines: Did / Doing /
   Blocked (or none)."
4. **Everyone posts.** Each person replies on their own feature post with one real progress
   line, and answers the standup. Post only real updates, and never on someone else's
   behalf.

### Screenshots for the Living Project Guide

| # | Screenshot |
|---|---|
| 1 | The #sprint-board list, with posts and their tags visible |
| 2 | The tag filter at the top set to **Doing** |
| 3 | One feature post with a teammate's progress reply |
| 4 | The standup post with at least two replies |
| 5 | Channel settings → Tags |
| 6 | The scheduled task **SpotBot sprint check-in** on claude.ai: its schedule and instructions |

Take them with Win+Shift+S. Crop out other servers, DMs and anything private. Put them in
section 6 of the guide: click a shaded box, delete its text, then Insert → Image → Upload from
computer, and keep the caption underneath.

## One-time setup (Nick, about 15 minutes)

1. **Make a separate bot.** In the Discord Developer Portal, create a new application called
   "SprintBot" and add a bot to it. Don't reuse SpotBot's token: this one goes into the
   cloud environment, so keep it separate from the product. On the Bot page, turn on
   **Message Content Intent** and leave the other two intents off. Without it, Discord's API
   returns people's replies with empty text, and `report` warns about it.
2. **Invite it to the team server.** Use the OAuth2 URL generator with the `bot` scope and these
   permissions: View Channels, Manage Channels (setup only), Send Messages, Send Messages
   in Threads, Create Public Threads, Manage Threads (to change tags) and Read Message History.
   That is permissions integer `326417583120`.
3. **Copy the server id.** Turn on Developer Mode in Discord, then right-click the server and
   choose Copy Server ID.
4. **Add the secrets to the Claude Code cloud environment** (claude.ai/code, then environment
   settings). Add `DISCORD_BOT_TOKEN` and `SPRINT_GUILD_ID` there, never in the repo or `.env`
   in git.
5. **Allow the network.** In the same environment's network settings, add `discord.com`. The routine
   can't reach Discord without it.
6. Run `setup`, then `seed --phase 2 --apply`, then fix any tag that's wrong (the plans lag
   the code: #2 is further along than its checkboxes say).

On your own machine you can run the same commands with the two variables set in your shell.

## Running it from Codex (teammates)

Only needed to run reports yourself; using the board needs nothing but Discord.

1. Use `main` once the tool is merged; until then, the branch
   `claude/socialagent-task-0-review-fjfksj`.
2. Get the SprintBot token and server id from Nick **by DM only**. Never paste the token in a
   channel, a commit or a `.env` in git; if it leaks, reset it in the Developer Portal.
3. In the Codex environment settings, add the secrets `DISCORD_BOT_TOKEN` and
   `SPRINT_GUILD_ID`, and allow internet access to `discord.com` (Codex blocks it by default).
4. Ask Codex: *"Read docs/SPRINT_BOARD.md, run `python3 scripts/sprint_board.py report` and show
   me the output. Don't commit, and never print the token."* No installs are needed.

`report` is always safe. Don't run `standup` or `seed --apply` yourself: the routine already
does, and running them twice makes duplicate posts.

## The routine

A scheduled Claude Code task, **"SpotBot sprint check-in"**, runs Mon/Wed/Fri at 8:51 a.m.
Pacific in a fresh cloud session and sends Nick a push notification when it finishes. It
follows the instructions below. If you change them here, update the scheduled task too.

1. Use `scripts/sprint_board.py` from `main` (or from the branch it was built on until it
   merges). Never commit or push.
2. If `DISCORD_BOT_TOKEN` or `SPRINT_GUILD_ID` is missing, or discord.com is unreachable,
   stop. Report which setup step above is missing, and post nothing.
3. `report --json --since 7` on Mondays, `--since 3` on Wednesdays and Fridays. If the JSON
   has a `warning`, put it in the final summary (it names the setup step to fix).
4. `standup`, which opens today's post and returns its id.
5. Write a check-in note of at most 1,500 characters and post it with `post --into`
   inside today's standup:
   - who replied to the last standup and who didn't;
   - every blocker someone named, quoted briefly;
   - Doing posts quiet for 5 or more days.
6. **Mondays only:** post `Sprint digest — week of <date>` with `post --title`:
   - 3–5 plain sentences on the week;
   - **Suggested moves**, matching `git log origin/main --since="7 days ago"` against post
     titles and feature numbers. For example: `#27 → Review (merged in a1b2c3d)`. The routine
     suggests moves and never makes them itself;
   - then the output of `report` (the markdown one), pasted unchanged.
7. Every number comes from the script's output and every claim from a reply or a commit;
   nothing is made up. Use names, never `@` or `<@id>`. Never print the token.
