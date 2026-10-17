# Sprint board: build plan

How the Discord agile tool goes from "built" to "runs itself". The tool is described in
[`SPRINT_BOARD.md`](SPRINT_BOARD.md); this page is the order to finish it in. Each stage
lists what to do, how to know it worked, and a prompt to paste into Claude Code (or Codex)
if you get stuck.

**Done means:** the team's `#sprint-board` forum keeps itself current, everyone answers the
Mon/Wed/Fri standup, and the scheduled Claude task posts the check-in without anyone running
a command.

| Stage | What | Who | When | Time |
|---|---|---|---|---|
| 0 | Script, tests, docs, scheduled task | Nick + Claude | Oct 16 | ✅ done |
| 1 | Make the bot and give the cloud environment its secrets | Nick | this weekend | 15 min |
| 2 | The bot takes over the hand-made board | Claude, new session | right after 1 | 5 min |
| 3 | Test-run the routine once | Claude | before Mon Oct 19 | 5 min |
| 4 | Merge the tool to main | Nick + one reviewer | week of Oct 19 | 30 min |
| 5 | The team uses it for a full week | everyone | Oct 19 – Oct 24 | 2 min a day |
| 6 | Upgrades, at most two | Nick | Phase 3 (Oct 27 – Nov 14) | 1–2 h each |

## Stage 0 — built (done)

- `scripts/sprint_board.py`: `setup`, `seed`, `report`, `standup`, `post`, `move`. Standard
  library only, never pings, never prints the token, retries Discord's rate limits.
- `test_sprint_board.py`: 20 offline tests against a fake Discord.
- The scheduled task **SpotBot sprint check-in**: Mon/Wed/Fri at 8:51 a.m. Pacific, a fresh
  session each run, push notification to Nick. First run: Mon Oct 19.
- The hand-made `#sprint-board` forum, with the Phase 2 posts and the first standup.

## Stage 1 — make the bot (Nick, 15 min)

1. **Create the app.** At discord.com/developers/applications, click **New Application** and
   name it `SprintBot`. Under **Bot**, click **Reset Token** and copy it. Don't paste it into
   a chat, the repo or `.env`.
2. **Turn on Message Content Intent** (same Bot page, Privileged Gateway Intents). Leave the
   other two off. Without it, Discord returns teammates' replies with no text, so the
   check-in can't quote blockers. A bot in fewer than 100 servers needs no review for this.
3. **Invite it.** Copy the **Application ID** from General Information, then open
   `https://discord.com/oauth2/authorize?client_id=APP_ID&scope=bot&permissions=326417583120`
   with your ID in place of `APP_ID`. That number is exactly: View Channels, Manage Channels,
   Send Messages, Send Messages in Threads, Create Public Threads, Manage Threads, Read
   Message History.
4. **Copy the server ID.** User Settings → Advanced → Developer Mode on. Right-click the
   server icon → Copy Server ID.
5. **Give the cloud environment the secrets.** In claude.ai/code, open the environment menu
   in the session's title bar → Edit. Add `DISCORD_BOT_TOKEN` and `SPRINT_GUILD_ID`. Under
   Network access, add `discord.com` to Allowed domains and keep "Allow package managers"
   ticked. Save. Use the environment the scheduled task runs in.

**Done when:** the environment shows both variables and `discord.com` is allowed.

> Stuck? Paste: *"I'm on Stage 1 step N of docs/SPRINT_BOARD_PLAN.md and I see ___. What do I
> click next?"*

## Stage 2 — the bot takes over the board (new session, 5 min)

Secrets only reach sessions started after you save them, so open a **new** session on the
repo and paste:

> *"Check out branch claude/socialagent-task-0-review-fjfksj. Confirm DISCORD_BOT_TOKEN and
> SPRINT_GUILD_ID are set (print only set/missing) and discord.com is reachable. Then run
> python3 scripts/sprint_board.py setup, then seed --phase 2 (dry run, show me), then seed
> --phase 2 --apply, then report --json --since 7. Tell me any warning. Don't commit
> anything or print the token."*

What should happen:

- `setup` finds your forum and adds only the missing tags (Phase 1, 3, 4 and Digest).
- `seed` skips the features you already posted and adds the missing Phase 2 ones (#20, #36).
- `report --json` shows your #27 reply's text and `"warning": null`.

**Done when:** `report` shows Phase 2 totalling 580 points, and there is no warning.

**If it goes wrong:**

| You see | Fix |
|---|---|
| `Set DISCORD_BOT_TOKEN…` | The session was opened before you saved: open another one. |
| `discord: … 403` | The bot is missing a permission: re-invite with the link in Stage 1. |
| A warning about Message Content Intent | Stage 1 step 2, then run `report` again. |

## Stage 3 — test-run the routine (before Monday, 5 min)

Paste into this session (or any session with the Claude Code Remote tools):

> *"Fire the SpotBot sprint check-in scheduled task once now, then tell me what it posted."*

A weekend run opens a standup post and writes the check-in note in it; the digest posts only
on Mondays. Delete the test standup afterwards if you don't want it.

**Done when:** the push notification says what it posted, and the note is in Discord.

> If the notification names a missing step, paste it: *"The sprint check-in said: ___. What
> do I fix?"*

## Stage 4 — merge to main (week of Oct 19)

Until it merges, the routine copies the script from this branch. That branch also holds
the design documents, so the tool goes in its own small pull request:

> *"Open a pull request to main with only scripts/sprint_board.py, test_sprint_board.py,
> docs/SPRINT_BOARD.md, docs/SPRINT_BOARD_PLAN.md and the docs/CAPSTONE_PLAN.md standup line,
> taken from claude/socialagent-task-0-review-fjfksj. Paste the pytest summary in the
> description."*

One teammate who didn't write it approves (AGENTS.md). Nothing else changes: the routine
already prefers `main` once the files are there.

**Done when:** the PR is merged and the next routine run uses `main`.

## Stage 5 — the team uses it (Oct 19 – Oct 24)

- Send the team message from `SPRINT_BOARD.md` (how the team uses it).
- At the next meeting, each owner moves 2–4 posts from Backlog to Doing.
- Mon/Wed/Fri: reply to the standup with **Did / Doing / Blocked**.
- After a PR merges, swap the post's tag to Review or Done. The Monday digest lists
  suggested moves; people make them.

**Done when:** three standups in a row have replies from at least three of the four of us,
and no Doing post is flagged stale on Friday Oct 23.

## Stage 6 — upgrades (Phase 3, pick at most two)

Each is its own small PR with tests, and none adds a paid service.

1. **Merged PR → reply on the feature post.** A GitHub Action on `pull_request: closed` (merged)
   finds `#N` in the PR title and posts "merged: <title> <link>" into that post through a
   Discord webhook (`?thread_id=`), with `allowed_mentions` empty. Needs one GitHub secret,
   `SPRINT_WEBHOOK_URL`. This replaces the routine's guessing from `git log`.
2. **Phase rollover.** On the first run of a new phase (Phase 3 starts Oct 27), the routine runs
   `seed --phase N` as a dry run and puts the list in the digest. Nick runs `--apply`.
3. **Points trend.** The Monday digest repeats the last digest's "Points done" line next to
   this week's, so the burndown shows without storing anything.

**Not planned:** slash commands (`/move`, `/standup`). They need a bot process running all
the time, which is a second deployment to host and secure. Discord's own tag picker already
does what `/move` would. Revisit only if the team asks for it.
