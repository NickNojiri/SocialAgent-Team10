"""Deploy a reviewed release tag to staging, or roll back to the last one (#11).

    python scripts/deploy.py v0.1.0              # deploy that tag
    python scripts/deploy.py v0.1.0 --dry-run    # check everything, change nothing
    python scripts/deploy.py --rollback          # back to the tag that was live before
    python scripts/deploy.py v0.1.0 --domain     # HTTPS from this host (see the compose files)

Runs on the staging host, in its clone of the repo. The rules it enforces:

- Only annotated tags named like v1.2.3 deploy, never a branch — so what's running
  is always something you can name and check out again.
- The tag's message must carry a `Security-Review: <name>` line: Track D's sign-off
  (#13) is the gate for a release. `--skip-review "<why>"` exists for an emergency
  and is written to the deploy log with its reason.
- After `docker compose up`, the admin app and the recommend service must report
  healthy and the bot and the share proxy must be running. If not, it goes back to
  the previous tag by itself and says so.

State lives in deploy/state.json (which tag is live, which was before) and every
attempt is appended to deploy/deploys.log — both stay on the host (gitignored).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

Runner = Callable[[list[str]], tuple[int, str]]

TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")
REVIEW_RE = re.compile(r"^Security-Review:[ \t]*(\S.*)$", re.MULTILINE)
MUST_BE_HEALTHY = ("admin", "recommend")
MUST_BE_RUNNING = ("bot", "share-proxy")


def mutates(cmd: list[str]) -> bool:
    """The commands --dry-run prints instead of running: switching the checkout and
    starting containers. Fetching, reading tags and `compose config`/`ps` still run."""
    return cmd[:2] == ["git", "checkout"] or (cmd[:2] == ["docker", "compose"] and "up" in cmd)


class DeployError(RuntimeError):
    pass


def compose_cmd(domain: bool = False) -> list[str]:
    cmd = ["docker", "compose", "-f", "docker-compose.staging.yml"]
    if domain:
        cmd += ["-f", "docker-compose.staging.domain.yml"]
    return cmd


def run(cmd: list[str]) -> tuple[int, str]:
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def env_problems(env_path: Path) -> list[str]:
    """What's missing from .env for staging. Values are never printed."""
    if not env_path.exists():
        return [f"{env_path} doesn't exist — copy .env.example and fill it in"]
    values = {}
    for line in env_path.read_text(encoding="utf-8").split("\n"):
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    problems = [f"{key} is empty" for key in ("DISCORD_TOKEN", "SPOTBOT_SIGNING_KEY", "SHARE_BASE_URL")
                if not values.get(key)]
    if values.get("SHARE_BASE_URL") and not values["SHARE_BASE_URL"].startswith("https://"):
        problems.append("SHARE_BASE_URL must start with https:// — share links carry a token")
    return problems


def reviewer_of(tag: str, runner: Runner) -> Optional[str]:
    """Who signed off on this tag, from its annotated message; None if nobody."""
    code, kind = runner(["git", "cat-file", "-t", tag])
    if code != 0 or kind.strip() != "tag":
        return None                                  # missing, or a lightweight tag
    _, message = runner(["git", "tag", "-l", "--format=%(contents)", tag])
    found = REVIEW_RE.search(message)
    return found.group(1).strip() if found else None


def service_states(runner: Runner, compose: list[str]) -> dict[str, dict]:
    code, out = runner(compose + ["ps", "--all", "--format", "json"])
    if code != 0:
        return {}
    out = out.strip()
    rows = json.loads(out) if out.startswith("[") else [json.loads(l) for l in out.splitlines() if l.strip()]
    return {r.get("Service"): r for r in rows}


def healthy(runner: Runner, compose: list[str], wait_s: float, sleep: Callable[[float], None],
            clock: Callable[[], float] = time.monotonic) -> bool:
    deadline = clock() + wait_s
    while True:
        states = service_states(runner, compose)
        ok = all(states.get(s, {}).get("Health") == "healthy" for s in MUST_BE_HEALTHY) and \
            all(states.get(s, {}).get("State") == "running" for s in MUST_BE_RUNNING)
        if ok:
            return True
        if clock() >= deadline:
            return False
        sleep(5)


class Deployer:
    def __init__(self, runner: Runner = run, *, root: Path = Path("."), domain: bool = False,
                 dry_run: bool = False, wait_s: float = 600, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, out=print):
        self._runner, self.dry_run, self.out = runner, dry_run, out
        self.root = Path(root)
        self.state_path = self.root / "deploy" / "state.json"
        self.log_path = self.root / "deploy" / "deploys.log"
        self.env_path = self.root / ".env"
        self.compose = compose_cmd(domain)
        self.wait_s, self.sleep, self.clock = wait_s, sleep, clock

    # ── plumbing ─────────────────────────────────────────────────────────────

    def runner(self, cmd: list[str]) -> tuple[int, str]:
        if self.dry_run and mutates(cmd):
            self.out("  would run: " + " ".join(cmd))
            return 0, ""
        return self._runner(cmd)

    def must(self, cmd: list[str], what: str) -> str:
        code, out = self.runner(cmd)
        if code != 0:
            raise DeployError(f"{what} failed:\n{out.strip()[-800:]}")
        return out

    def state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save_state(self, current: str, previous: Optional[str]) -> None:
        if self.dry_run:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"current": current, "previous": previous}, indent=2),
                                   encoding="utf-8", newline="\n")

    def log(self, line: str) -> None:
        self.out(line)
        if self.dry_run:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.log_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{stamp} {line}\n")

    def switch_to(self, tag: str) -> bool:
        """Check out a tag, rebuild, start, and wait for health. True if healthy."""
        self.must(["git", "checkout", "--detach", tag], f"checking out {tag}")
        self.must(self.compose + ["up", "-d", "--build", "--remove-orphans"], "docker compose up")
        if self.dry_run:
            return True
        return healthy(self.runner, self.compose, self.wait_s, self.sleep, self.clock)

    # ── the two commands ─────────────────────────────────────────────────────

    def deploy(self, tag: str, skip_review: Optional[str] = None) -> str:
        if not TAG_RE.match(tag):
            raise DeployError(f"{tag!r} isn't a release tag (v1.2.3) — staging only runs tags")
        problems = env_problems(self.env_path)
        if problems:
            raise DeployError("fix .env first:\n  - " + "\n  - ".join(problems))
        self.must(["git", "fetch", "--tags", "origin"], "git fetch")
        reviewer = reviewer_of(tag, self.runner)
        if reviewer is None and not skip_review:
            raise DeployError(
                f"{tag} has no Security-Review line, so Track D hasn't signed off (#13).\n"
                f"  Tag it as:  git tag -a {tag} -m \"<notes>\" -m \"Security-Review: <name>\"")
        self.must(self.compose + ["config", "--quiet"], "docker compose config")
        previous = self.state().get("current")
        who = f"reviewed by {reviewer}" if reviewer else f"REVIEW SKIPPED: {skip_review}"
        if self.switch_to(tag):
            self.save_state(tag, previous)
            self.log(f"deployed {tag} ({who}); previous {previous or 'none'}")
            return tag
        self.log(f"FAILED {tag} ({who}): not healthy within {int(self.wait_s)}s")
        if previous and self.switch_to(previous):
            self.log(f"rolled back to {previous}")
            raise DeployError(f"{tag} didn't come up healthy; rolled back to {previous}")
        raise DeployError(f"{tag} didn't come up healthy and there was nothing healthy to roll back to")

    def rollback(self) -> str:
        state = self.state()
        previous, current = state.get("previous"), state.get("current")
        if not previous:
            raise DeployError("no previous deploy recorded in deploy/state.json")
        if not self.switch_to(previous):
            raise DeployError(f"{previous} didn't come up healthy either — check `docker compose ps`")
        self.save_state(previous, current)
        self.log(f"rolled back from {current} to {previous}")
        return previous


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tag", nargs="?", help="release tag, e.g. v0.1.0")
    ap.add_argument("--rollback", action="store_true", help="go back to the previous tag")
    ap.add_argument("--dry-run", action="store_true", help="check everything, change nothing")
    ap.add_argument("--domain", action="store_true", help="also use docker-compose.staging.domain.yml")
    ap.add_argument("--skip-review", metavar="WHY", help="emergency only; logged with the reason")
    args = ap.parse_args(argv)
    if bool(args.tag) == args.rollback:
        ap.error("give a tag, or --rollback")
    deployer = Deployer(domain=args.domain, dry_run=args.dry_run)
    try:
        live = deployer.rollback() if args.rollback else deployer.deploy(args.tag, args.skip_review)
    except DeployError as exc:
        print(f"deploy: {exc}", file=sys.stderr)
        return 1
    print(f"{'(dry run) ' if args.dry_run else ''}live: {live}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
