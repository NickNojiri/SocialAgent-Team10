"""Replace SPOTBOT_SIGNING_KEY with a new random key (feature #10, threat model T6).

    python scripts/rotate_signing_key.py            # explains what would happen, changes nothing
    python scripts/rotate_signing_key.py --confirm  # rotates

Rotating is how you recover from a leaked key: every token signed with the old one
stops verifying the moment the services restart with the new one. That includes every
/share link ever sent — they all break, and have to be re-issued with /share. So this
refuses to run without --confirm.

What it does:
  1. copies the current .env to data/key-backups/ (data/ is gitignored; the copy is made
     owner-only where the OS allows it), so a mistake can be undone;
  2. writes a fresh key in place of the old one, touching nothing else in the file;
  3. prints what to restart and what just broke.

It never prints either key, not even a prefix. The key line is found with
ensure_signing_key.py's pattern, which already survived two CRLF bugs.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Reuse the tested parser rather than a second regex for the same line.
_spec = importlib.util.spec_from_file_location(
    "ensure_signing_key", Path(__file__).with_name("ensure_signing_key.py")
)
_esk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_esk)
ENV_VAR = _esk.ENV_VAR
_LINE = _esk._LINE


class RotationRefused(Exception):
    """Nothing was changed; the message says why."""


@dataclass
class Rotation:
    backup: Path          # where the previous .env now lives


def _current_key(text: str) -> tuple[object, str]:
    match = _LINE.search(text)
    value = match.group(1).strip().strip('"').strip("'") if match else ""
    return match, value


def rotate(env_path: Path, backup_dir: Path, now: datetime | None = None) -> Rotation:
    """Swap the key in `env_path`, keeping a backup in `backup_dir`."""
    if not env_path.exists():
        raise RotationRefused(f"no {env_path.name} here — nothing to rotate")
    raw = env_path.read_bytes()
    text = raw.decode("utf-8-sig")
    match, old = _current_key(text)
    if match is None or not old:
        raise RotationRefused(
            f"{env_path.name} has no {ENV_VAR} yet — run scripts/ensure_signing_key.py "
            "to create one; there is nothing to rotate"
        )

    new = secrets.token_hex(32)
    while new == old:                          # astronomically unlikely; never ship it
        new = secrets.token_hex(32)

    # 1. Backup first, so a failure below can't leave you with neither key.
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"env-{stamp}.bak"
    n = 1
    while backup.exists():                     # two rotations in one second
        backup = backup_dir / f"env-{stamp}-{n}.bak"
        n += 1
    backup.write_bytes(raw)
    _owner_only(backup)

    # 2. Replace just the value, then swap the file in atomically.
    text = text[: match.start()] + f"{ENV_VAR}={new}" + text[match.end():]
    # The temp file holds the new key, so it must never be left lying around.
    tmp = env_path.with_name(env_path.name + ".rotating")
    try:
        tmp.write_bytes(text.encode("utf-8"))
        _owner_only(tmp)
        os.replace(tmp, env_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return Rotation(backup=backup)


def _owner_only(path: Path) -> None:
    """chmod 600 on macOS/Linux. On Windows os.chmod can't restrict readers; the file
    keeps the ACL of its folder (your user profile), which the runbook says to check."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


EXPLAIN = f"""Rotating {ENV_VAR} will:
  - back up .env to data/key-backups/ (gitignored)
  - write a new key into .env
  - BREAK every /share link already sent, the moment the services restart
Nothing has been changed. To rotate, run again with --confirm."""

AFTER = """Rotated {var}. Backup of the old .env: {backup}

Now, in this order:
  1. restart the admin app (:8010) and the recommend service (:8003)
  2. restart the bot — it signs requests, so until it restarts every call it makes is refused
  3. tell your servers their old share links stopped working; /share makes new ones

To undo before anyone uses the new key: copy the backup back over .env and restart all three.
Delete the backup once you're sure — it holds the old key."""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--confirm", action="store_true", help="actually rotate (breaks share links)")
    args = ap.parse_args(argv)
    if not args.confirm:
        print(EXPLAIN)
        return 2
    try:
        result = rotate(REPO / ".env", REPO / "data" / "key-backups")
    except RotationRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(AFTER.format(var=ENV_VAR, backup=result.backup.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
