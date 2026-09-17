"""Make sure .env exists and carries a SPOTBOT_SIGNING_KEY.

    python scripts/ensure_signing_key.py

Creates .env from .env.example if there is none, then fills SPOTBOT_SIGNING_KEY
with a fresh random key if it is missing or empty. An existing key is never
replaced — rotating it would revoke every /share link already sent — and nothing
else in the file is touched. Safe to run any number of times; setup.ps1 runs it.
"""

from __future__ import annotations

import re
import secrets
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_VAR = "SPOTBOT_SIGNING_KEY"
# [ \t] and [^\r\n], never \s: on a CRLF file \s would run past the end of an
# empty "KEY=" line and read the next line's text as the value.
_LINE = re.compile(rf"^{ENV_VAR}[ \t]*=[ \t]*([^\r\n]*?)[ \t]*(?=\r?$)", re.MULTILINE)


def ensure(env_path: Path, example_path: Path) -> str:
    """Returns 'created', 'filled', 'appended' or 'kept'."""
    created = False
    if not env_path.exists():
        if not example_path.exists():
            raise SystemExit(f"no {example_path.name} to copy from — run this from the repo")
        env_path.write_bytes(example_path.read_bytes())
        created = True

    raw = env_path.read_bytes()
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if b"\r\n" in raw else "\n"
    key = secrets.token_hex(32)

    match = _LINE.search(text)
    if match and match.group(1).strip().strip('"').strip("'"):
        return "created" if created else "kept"
    if match:
        text = text[: match.start()] + f"{ENV_VAR}={key}" + text[match.end():]
        status = "filled"
    else:
        if text and not text.endswith(("\n", "\r")):
            text += newline
        text += f"{ENV_VAR}={key}{newline}"
        status = "appended"
    env_path.write_bytes(text.encode("utf-8"))
    return "created" if created else status


def main() -> None:
    status = ensure(REPO / ".env", REPO / ".env.example")
    messages = {
        "created": f"created .env with a new {ENV_VAR}",
        "filled": f"filled in {ENV_VAR} in .env",
        "appended": f"added {ENV_VAR} to .env",
        "kept": f".env already has a {ENV_VAR} — left it alone",
    }
    print(messages[status])
    if status != "kept":
        print("restart the admin app, the recommend service and the bot so they all pick it up")


if __name__ == "__main__":
    sys.exit(main())
