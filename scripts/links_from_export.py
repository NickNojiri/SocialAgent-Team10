"""Pull Instagram reel/post links out of Instagram's own "Download your information"
export — the compliant replacement for scripts/pull_dm_reels.py.

pull_dm_reels.py logs in with an unofficial client and reads DMs as a program, which
Instagram's Terms of Use don't permit (the burner was auto-flagged on 2026-10-09).
The export is Instagram's official copy of your own data, so nothing here touches
Instagram at all: it only reads files you downloaded.

  1. In the Instagram app (on the account the reels were sent to):
     Settings -> Accounts Center -> Your information and permissions ->
     Download your information -> Messages -> format JSON.
  2. Unzip the file Instagram emails you (or pass the .zip directly).
  3. python scripts/links_from_export.py path/to/export.zip --out reels.txt

Output: one https://www.instagram.com/reel/<code>/ per line, de-duplicated, sorted.
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Iterable, Iterator

_CODE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")


def _strings(obj) -> Iterator[str]:
    """Every string anywhere in a JSON value — the export's schema changes often."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def codes_from_json_texts(texts: Iterable[str]) -> set[str]:
    codes: set[str] = set()
    for text in texts:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue                          # not every file in an export is JSON
        for s in _strings(data):
            codes.update(_CODE.findall(s))
    return codes


def _message_files(src: Path) -> Iterator[str]:
    """JSON text of every messages/**/*.json file in an export folder or .zip."""
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                if "messages/" in name and name.endswith(".json"):
                    yield z.read(name).decode("utf-8", errors="replace")
    else:
        for p in src.rglob("*.json"):
            if "messages" in p.parts:
                yield p.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", type=Path, help="the unzipped export folder, or the .zip")
    ap.add_argument("--out", type=Path, help="also write the URL list here")
    args = ap.parse_args()

    urls = sorted(f"https://www.instagram.com/reel/{c}/" for c in codes_from_json_texts(_message_files(args.export)))
    print("\n".join(urls))
    if args.out:
        args.out.write_text("\n".join(urls) + "\n", encoding="utf-8", newline="\n")
    print(f"\n{len(urls)} links", flush=True)


if __name__ == "__main__":
    main()
