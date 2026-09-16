# Setting up Codex for SpotBot (Mac and Windows)

For teammates using **Codex** with a **CSULB ChatGPT Edu** account. Written 2026-09-16
from OpenAI's docs at the time ([Codex](https://learn.chatgpt.com/docs/cloud),
[CLI](https://learn.chatgpt.com/docs/codex/cli),
[cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environment)) —
if a button has moved, the docs win.

**The short version:** do Parts 1–3 once. Then pick *one* way to run Codex — in the
browser (Part 4) or on your laptop (Part 5) — and use Part 6 every time you work.

The repo already has an **`AGENTS.md`** at its root. Codex reads it automatically: the
test commands, the files it must never commit, and which folders belong to your track are
in there. You don't need to paste any of that into your prompts.

---

## Part 1 — Check your account (2 minutes)

1. Go to **https://chatgpt.com/codex** and sign in with your **CSULB** account.
2. If you see the Codex page, you're set. If it says Codex isn't available, the school's
   workspace admin hasn't enabled it — tell Nick, and use Part 5 in the meantime only if
   *local* Codex works for you (admins can enable local and cloud separately).

Edu plans include Codex; usage limits apply per person.

## Part 2 — GitHub (5 minutes)

You need a GitHub account with write access to the repo to open pull requests.

1. Make a GitHub account if you don't have one, and send **Nick your GitHub username**.
2. Accept the collaborator invite that arrives by email (or at
   https://github.com/NickNojiri/SocialAgent-Team10/invitations).
3. Tell git who you are (once per laptop — use the email on your GitHub account):
   ```bash
   git config --global user.name "Your Name"
   git config --global user.email "you@example.com"
   ```

## Part 3 — Get the project on your laptop

Do this even if you plan to use Codex in the browser — you need it to run things yourself
and to check Codex's work. **Do it at home, not on campus Wi-Fi.**

### Mac

```bash
xcode-select --install                 # git + make; skip if already installed
brew install python@3.12 gh            # needs Homebrew: https://brew.sh
# Ollama: download from https://ollama.com/download and open it once

git clone https://github.com/NickNojiri/SocialAgent-Team10.git
cd SocialAgent-Team10
python3.12 -m venv .venv               # name the version: plain python3 may be Apple's 3.9
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
ollama pull mxbai-embed-large          # required for the catalog (~0.7 GB)
.venv/bin/python -m pytest -k "not live" -q
```

No Homebrew and don't want it? Install Python 3.12 from https://www.python.org/downloads/
(it also provides `python3.12`) and the GitHub CLI from https://cli.github.com instead.

### Windows

```powershell
winget install --id Git.Git
winget install --id Python.Python.3.12
winget install --id GitHub.cli
# Ollama: download from https://ollama.com/download
# close and reopen PowerShell so the new commands are found

git clone https://github.com/NickNojiri/SocialAgent-Team10.git
cd SocialAgent-Team10
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
.\.venv\Scripts\python.exe -m pytest -k "not live" -q
```

**Both:** the last command should end with `253 passed` (or more). Then log GitHub in, so
you can push branches:
```bash
gh auth login        # choose GitHub.com → HTTPS → log in with a web browser
```

---

## Part 4 — Option A: Codex in the browser (cloud)

Easiest to start. Codex works in a Linux sandbox on OpenAI's servers and hands you a
pull request. **Good for Tracks B and C. Not for Track A's first task** (it needs live
Instagram, and the sandbox has no internet while the agent works).

**One-time setup:**

1. Open **https://chatgpt.com/codex** → connect **GitHub** when prompted → allow access to
   **NickNojiri/SocialAgent-Team10**.
2. Go to **Codex settings → Environments**
   (https://chatgpt.com/codex/settings/environments) and create an environment for
   the repo.
3. Under **Set package versions**, pick **Python 3.12**.
4. **Setup script** — paste exactly:
   ```bash
   pip install -r requirements.txt
   python -m playwright install --with-deps chromium
   ```
   (If the second line fails, change it to `python -m playwright install chromium`.)
5. **Agent internet access:** leave it **off**. Nothing in Tracks B or C needs it.
6. **Environment variables / secrets:** add **none**. The tests don't need a Discord token,
   and a token must never go into Codex.

**Each task:** pick the environment → describe the task (see Part 7) → watch the log →
read the summary and the diff → ask for fixes if needed → **Create PR**. Then do Part 6
step 6 before asking for a review.

## Part 5 — Option B: Codex on your laptop

Codex edits the repo on your machine. Works for every track, including Track A. Use
**either** the VS Code extension **or** the terminal CLI.

### VS Code extension (recommended if you're new to terminals)

1. In VS Code, open **Extensions**, search **`openai.chatgpt`**, install it.
2. Sign in with your CSULB ChatGPT account when asked.
3. **File → Open Folder…** → the `SocialAgent-Team10` folder.
4. Click the **Codex** icon in the sidebar (or Command Palette → **Codex: Open Codex
   Sidebar**).

### Terminal CLI

Install:

| | Command |
|---|---|
| **Mac** | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` — or `brew install --cask codex` |
| **Windows** (PowerShell) | `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 \| iex"` |
| either, if you have Node.js | `npm install -g @openai/codex` |

Then:
```bash
cd SocialAgent-Team10
codex                 # first run: choose "Sign in with ChatGPT" — a browser window opens
```
Inside Codex, **`/permissions`** controls what it may do without asking. Start with the
setting that asks before running commands, until you're comfortable.

Windows runs Codex natively in PowerShell with its own sandbox; WSL2 also works if you
already use it.

### Desktop app (optional)

A standalone Codex app exists — Mac: https://persistent.oaistatic.com/codex-app-prod/Codex.dmg ·
Windows: `winget install --id 9PLM9XGG6VKS -s msstore`. Open it, sign in, then **Add new
project** → the repo folder. Same workflow as the extension.

---

## Part 6 — The workflow, every task

1. **Start from fresh `main`:**
   ```bash
   git checkout main
   git pull
   git checkout -b trackB/label-fixes          # track + a short name
   ```
2. **Give Codex the task** (Part 7). Ask for a plan first; read it; say go.
3. **Let it run the tests.** It should show you the pytest summary line.
4. **Read the diff yourself** — VS Code's Source Control panel, or `git diff`. If a line
   doesn't make sense, ask Codex *"explain this change line by line"*. If you still can't
   explain it, it doesn't go in the PR.
5. **Run the check yourself** — Mac `.venv/bin/python -m pytest -k "not live" -q`,
   Windows `.\.venv\Scripts\python.exe -m pytest -k "not live" -q`.
6. **Before the PR, confirm nothing secret is in it:** `git status` must not list `.env`,
   anything under `data/`, or `fixtures/labels.batch*.jsonl`.
7. **Commit, push, open the PR:**
   ```bash
   git add <the files you changed>
   git commit -m "fix(labels): apply the two pending label fixes"
   git push -u origin HEAD
   gh pr create --fill
   ```
   (Cloud tasks: Codex's **Create PR** does this.) Post the link in Discord and ask one
   teammate to review. Don't merge your own.

## Part 7 — Prompts that work on this repo

**Always start with:**
> Follow AGENTS.md. Read the files I name before changing anything. Explain your plan in
> three sentences and wait for me to say go. When done, run the offline test suite and
> show me the summary line.

**Track A — first task** (laptop only, needs internet):
> In test_ig_live.py, add the 6 reel URLs listed in docs/REEL_TRANSCRIBE_TEST_PLAN.md to
> IG_URLS so there are 19. Don't change any other code. Then tell me the exact command to
> run the live test myself — don't run it. After I paste you the output, write
> docs/BASELINE.md with one row per URL: link, result (ok / login wall / no video /
> timeout / no venue), and rough time.

**Track B — first task:**
> In fixtures/labels.jsonl, change exactly two rows and nothing else. Row with url
> containing DYh-C2FPiGS: gold.category → "cafe_dessert", gold.venue → "The First Take".
> Row with url containing DZzvbTupPND: note → "name_in_comments". Keep one JSON object per
> line, UTF-8, LF. Run `python -m src.ingestion.eval --offline --split test` before and
> after, run `python scripts/build_aliases.py`, then `python -m pytest
> test_extraction_labels.py -q`. Show me both scorecards and explain whether the test
> split moved, given both rows are in the train split.

**Track C — first task:**
> Read app/test_bot.py. Add one test to TestOnMessageRouting: a message containing
> https://www.instagram.com/reel/AAA111/?igsh=x, https://instagram.com/p/BBB222, and the
> first link again must call handle_reel_capture exactly once with
> ["https://www.instagram.com/reel/AAA111/", "https://instagram.com/p/BBB222/"]. Follow
> the existing tests' style. Don't change bot.py. Run `python -m pytest app/test_bot.py -q`.

**When you're stuck:**
> Explain what this file does and how it connects to the rest of the project, in plain
> language, before we change anything: <path>

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Codex page says it isn't available | CSULB admin setting — tell Nick; local and cloud can be enabled separately |
| `running scripts is disabled` (Windows) | use the `powershell -ExecutionPolicy Bypass -File …` form above |
| Cloud setup script fails on Playwright | change the second line to `python -m playwright install chromium` |
| Codex wants internet to finish a Track B/C task | it shouldn't — tell it the task is offline and to read AGENTS.md |
| Tests pass for Codex but fail for you | you're on an old `main` — `git checkout main && git pull`, then rebase your branch |
| `git push` rejected: permission denied | you haven't accepted the collaborator invite, or `gh auth login` wasn't run |
| Certificate / SSL errors on install or push | you're on campus Wi-Fi — go home or use a hotspot |
| Codex edited files outside your track | say *"revert changes outside <your folders> — see the ownership table in AGENTS.md"* |
| `.env` or `data/` shows up in `git status` | don't commit it; `git restore --staged <file>` and tell Nick |
