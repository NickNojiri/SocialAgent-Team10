"""Make M2 better with Nick's own Instagram data, and prove it on reels it never saw.

One script, five steps. Everything it writes goes under data/my_export/ (git-ignored):
the export holds friends' DMs, and the repo is public.

    python experiments/burner_study/my_export.py extract <export.zip or folder>
    python experiments/burner_study/my_export.py label            # ~10 s a reel; quit any time, it resumes
    python experiments/burner_study/my_export.py label --heldout  # the reels kept for scoring (do ~50+)
    python experiments/burner_study/my_export.py train            # 6 runs, hours on CPU: run it overnight
    python experiments/burner_study/my_export.py report

What each step does, and the rules it keeps (docs/DATA_PLAN.md, AGENTS.md):

  extract  Reads the "Download your information" export (JSON or HTML) on this machine: every
           reel a DM shared, with the post's own caption and poster handle. It never keeps the
           words people typed to each other, or friends' names. Reels already in the corpus or
           the burner inbox are dropped (they're already used). The rest are sorted with the
           project's own split rule (eval.split_of, by creator):
             train -> "train pool": these can be trained on.
             dev   -> "held-out pool": never trained on; scored as "your reels".
             test  -> dropped. The test split is read once, at the end, by eval.py --final.
  label    Nick says which place each reel is about. The candidate names are shown in the
           order the caption offers them, without saying which one the rules or M2 chose, so
           the label can't lean toward either. Labels are gold (labeler "nick").
  train    Runs M2 v5 (the recommended recipe: --clean --parser-pick --recipe-cue --dev-plus,
           5 epochs) twice per seed, 13/14/15: once as before ("base") and once with the train
           pool added ("mine"). The dev reels are the same in both arms, so the comparison is
           paired. Each run also scores the held-out pool.
  report   Parser vs base M2 vs new M2, on the frozen dev split and on your held-out reels,
           per seed, with the paired confidence interval. It says "better" only when the gain
           holds on every seed and the interval is above zero.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.ingestion import eval as ev  # noqa: E402

OUT = REPO / "data" / "my_export"
SEEDS = (13, 14, 15)
V5 = ["--clean", "--parser-pick", "--recipe-cue", "--dev-plus", "--epochs", "5"]
_CODE = re.compile(r"instagram\.com/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,40})")
_HANDLE = re.compile(r"^@?[A-Za-z0-9._]{1,30}$")
_ATTACHMENT = re.compile(r"sent an attachment\.?$", re.I)


# ── 1. extract ────────────────────────────────────────────────────────────────

def fix_text(s: Optional[str]) -> str:
    """The JSON export writes UTF-8 bytes as \\u00XX escapes ('MayÃ¡' -> 'Mayá')."""
    s = s or ""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def shares_from_json(text: str) -> Iterator[dict]:
    """Reels shared in one JSON thread. Only the share's own fields are read (the post's
    caption and owner), never a message's typed 'content'."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return
    for m in (data.get("messages") or []) if isinstance(data, dict) else []:
        share = m.get("share") or {}
        code = _CODE.search(share.get("link") or "")
        if code:
            yield {"code": code.group(1), "caption": fix_text(share.get("share_text")).strip(),
                   "handle": fix_text(share.get("original_content_owner")).strip().lstrip("@"),
                   "sender": fix_text(m.get("sender_name")), "ts": m.get("timestamp_ms") or 0}


class _ThreadHTML(HTMLParser):
    """Splits one HTML thread into messages: [sender, [text or ('link', href), ...]].
    A message starts at its <h2> (the sender); each <div>'s text is one item."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.messages: list[list] = []
        self._buf: list[str] = []
        self._in_h2 = self._in_a = False

    def _flush(self):
        text = "".join(self._buf).strip()
        self._buf = []
        if text and self.messages:
            self.messages[-1][1].append(text)

    def handle_starttag(self, tag, attrs):
        if tag == "h2":
            self._flush()
            self._in_h2 = True
            self.messages.append(["", []])
        elif tag == "div":
            self._flush()
        elif tag == "br":
            self._buf.append("\n")
        elif tag == "a":
            self._flush()
            self._in_a = True
            href = dict(attrs).get("href") or ""
            if self.messages:
                self.messages[-1][1].append(("link", href))

    def handle_endtag(self, tag):
        if tag == "h2":
            self._in_h2 = False
            if self.messages:
                self.messages[-1][0] = "".join(self._buf).strip()
            self._buf = []
        elif tag == "a":
            self._in_a = False
            self._buf = []
        elif tag == "div":
            self._flush()

    def handle_data(self, data):
        if not self._in_a:
            self._buf.append(data)


def shares_from_html(text: str) -> Iterator[dict]:
    """Reels shared in one HTML thread. A share renders as caption, poster handle, link.
    The caption is kept only when the poster's handle sits right before the link: a link
    someone typed into a message has no handle, and its words are private, so they're dropped."""
    p = _ThreadHTML()
    p.feed(text)
    for sender, items in p.messages:
        texts: list[str] = []
        for item in items:
            if isinstance(item, tuple):
                code = _CODE.search(item[1])
                if code:
                    handle = texts[-1] if texts and _HANDLE.match(texts[-1]) else ""
                    caption = texts[-2] if handle and len(texts) >= 2 else ""
                    yield {"code": code.group(1), "caption": caption, "handle": handle.lstrip("@"),
                           "sender": sender, "ts": 0}
                texts = []
            elif not _ATTACHMENT.search(item):
                texts.append(item)


def _thread_files(src: Path) -> Iterator[tuple[str, str]]:
    """(thread name, text) for every messages/inbox/<thread>/message_N file."""
    def keep(name: str) -> bool:
        return "messages/" in name.replace("\\", "/") and name.endswith((".json", ".html"))

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            for name in z.namelist():
                if keep(name):
                    yield name.replace("\\", "/").rsplit("/", 2)[-2], z.read(name).decode("utf-8", errors="replace")
    else:
        for p in sorted(src.rglob("*")):
            if p.is_file() and keep(p.as_posix()):
                yield p.parent.name, p.read_text(encoding="utf-8", errors="replace")


def read_export(src: Path) -> list[dict]:
    """Every shared reel, once (the longest caption wins), marked shared by "me" or "friend".
    "Me" is the sender seen in the most threads; names themselves are not kept."""
    shares, threads_of = [], {}
    for thread, text in _thread_files(src):
        found = list(shares_from_json(text) if text.lstrip().startswith("{") else shares_from_html(text))
        for s in found:
            threads_of.setdefault(s["sender"], set()).add(thread)
        shares += found
    me = max(threads_of, key=lambda k: len(threads_of[k])) if threads_of else ""
    best: dict[str, dict] = {}
    from_friend = {s["code"] for s in shares if s["sender"] != me}
    for s in shares:
        cur = best.get(s["code"])
        if cur is None or len(s["caption"]) > len(cur["caption"]):
            best[s["code"]] = {"code": s["code"], "caption": s["caption"], "handle": s["handle"] or (cur or {}).get("handle", ""),
                               "shared_by": "friend" if s["code"] in from_friend else "me"}
    return list(best.values())


def known_codes() -> set[str]:
    """Reels the project already has: corpus, frozen splits, burner inbox."""
    codes = {ev._code(r["url"]) for r in ev.load_labels()}
    codes |= set(json.loads(ev.SPLITS_PATH.read_text(encoding="utf-8")).get("rows", {}))
    inbox = REPO / "fixtures" / "labels.inbox.jsonl"
    if inbox.exists():
        for line in inbox.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                codes.add(ev._code(json.loads(line)["url"]))
    return codes


def to_row(reel: dict) -> dict:
    return {"url": f"https://www.instagram.com/reel/{reel['code']}/",
            "input": {"caption": reel["caption"], "handle": reel["handle"], "og_title": "",
                      "ocr": "", "transcript": "", "hashtags": []},
            "shared_by": reel["shared_by"]}


def pool_of(row: dict) -> str:
    """'train' | 'dev' | 'test' by the project's split rule, ignoring any silver mark (a
    machine label must not pull a dev creator's reel into training)."""
    return ev.split_of({k: v for k, v in row.items() if k != "source"})


def split_reels(reels: Iterable[dict], known: set[str]) -> tuple[list[dict], list[dict], Counter]:
    train, held, n = [], [], Counter()
    for reel in reels:
        if reel["code"] in known:
            n["already in the project"] += 1
            continue
        row = to_row(reel)
        pool = pool_of(row)
        n[pool] += 1
        if pool == "train":
            train.append({**row, "pool": "train"})
        elif pool == "dev":
            held.append({**row, "pool": "dev"})
    return train, held, n


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def cmd_extract(args) -> None:
    reels = read_export(Path(args.export))
    train, held, n = split_reels(reels, known_codes())
    write_jsonl(OUT / "reels.jsonl", train + held)
    with_caption = sum(bool(r["input"]["caption"]) for r in train + held)
    print(f"{len(reels)} reels shared in your DMs ({sum(r['shared_by'] == 'friend' for r in reels)} from friends)")
    print(f"  already in the project : {n['already in the project']}")
    print(f"  train pool             : {n['train']}  (can be trained on)")
    print(f"  held-out pool          : {n['dev']}  (scored as 'your reels', never trained on)")
    print(f"  test creators, dropped : {n['test']}")
    print(f"  with a caption         : {with_caption} of {len(train) + len(held)}")
    print(f"wrote {OUT / 'reels.jsonl'}. Next: label")


# ── 2. label ──────────────────────────────────────────────────────────────────

def _sq(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def labeled_row(row: dict, venue: Optional[str], rules_pick: Optional[str]) -> dict:
    """A corpus-shaped gold row. venue None means "not about one place" (in_catalog False)."""
    verdict = "right" if _sq(rules_pick) == _sq(venue) else ("missing" if not rules_pick else "wrong")
    return {**row, "predicted": {"venue": rules_pick, "city": None, "category": None},
            "gold": {"venue": venue, "city": None, "category": None, "in_catalog": venue is not None},
            "verdict": {"venue": verdict}, "labeler": "nick", "source": "my_export"}


def menu(row: dict) -> tuple[Optional[str], list[str]]:
    """The rules' pick (kept hidden) and the candidate names, in caption order."""
    from src.ingestion.pipeline import normalizer as nz
    from src.ingestion.pipeline.place_picker import candidates

    raw = ev.raw_from_input(row["url"], row["input"])
    pick = nz.normalize(raw).get("venue_name")
    names = [c.name for c in candidates(raw, None)]
    if pick and _sq(pick) not in {_sq(n) for n in names}:
        names.append(pick)
    return pick, names


def cmd_label(args, ask=input, say=print) -> int:
    pool = "dev" if args.heldout else "train"
    out = OUT / ("labels.heldout.jsonl" if args.heldout else "labels.jsonl")
    done = {r["url"] for r in read_jsonl(out)}
    todo = [r for r in read_jsonl(OUT / "reels.jsonl") if r["pool"] == pool and r["url"] not in done]
    random.Random(7).shuffle(todo)                  # mixes friends and posters; same order every run
    say(f"{len(done)} labeled, {len(todo)} to go. Number = that place, 0 = not about one place "
        "(recipe, list, meme), or type the right name. s = skip, q = quit.")
    labeled = 0
    for row in todo[: args.limit or None]:
        pick, names = menu(row)
        cap = row["input"]["caption"] or "(no caption in the export)"
        say("\n" + "─" * 60 + f"\n@{row['input']['handle'] or '?'}  {row['url']}\n{cap[:600]}")
        for i, n in enumerate(names, 1):
            say(f"  {i}. {n}")
        while True:
            a = ask("> ").strip()
            if a.lower() == "q":
                return labeled
            if a.lower() == "s":
                break
            if a == "0":
                venue = None
            elif a.isdigit() and 1 <= int(a) <= len(names):
                venue = names[int(a) - 1]
            elif len(a) >= 2 and not a.isdigit():
                venue = a
            else:
                say("  a number from the list, 0, a name, s or q")
                continue
            with out.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(labeled_row(row, venue, pick), ensure_ascii=False) + "\n")
            labeled += 1
            break
    say(f"\n{labeled} labeled this time.")
    return labeled


# ── 3. train ──────────────────────────────────────────────────────────────────

def load_extra(path: Path, pool: str, used: set[str]) -> list[dict]:
    """Labeled rows from `path` that are safe to use as `pool` ('train' or 'dev'): the right
    pool by the split rule, scoreable, and not a reel the trainer already has."""
    keep = []
    for r in read_jsonl(Path(path)):
        gold, code = r.get("gold") or {}, ev._code(r["url"])
        if code in used or pool_of(r) != pool:
            continue
        if (r.get("verdict") or {}).get("venue") in ("right", "wrong", "missing") and (
                gold.get("venue") or gold.get("in_catalog") is False):
            keep.append({k: v for k, v in r.items() if k != "source"})   # gold, full weight
    return keep


def runs(seeds: Iterable[int]) -> list[tuple[str, int, list[str]]]:
    trainer = str(HERE / "ml_reranker.py")
    held = ["--extra-dev", str(OUT / "labels.heldout.jsonl")] if (OUT / "labels.heldout.jsonl").exists() else []
    out = []
    for s in seeds:
        for arm in ("base", "mine"):
            extra = ["--extra", str(OUT / "labels.jsonl")] if arm == "mine" else []
            out.append((arm, s, [sys.executable, trainer, *V5, "--seed", str(s), "--tag", f"my_{arm}_s{s}",
                                 *extra, *held, "--preds-out", str(OUT / "preds" / f"{arm}_s{s}.jsonl")]))
    return out


def cmd_train(args) -> None:
    n = len(read_jsonl(OUT / "labels.jsonl"))
    if n < 30 and not args.force:
        raise SystemExit(f"only {n} labeled train-pool reels; label at least 30 first (or pass --force)")
    for arm, seed, cmd in runs(args.seeds):
        pred = OUT / "preds" / f"{arm}_s{seed}.jsonl"
        if pred.exists() and not args.again:
            print(f"skip {arm} seed {seed}: {pred.name} exists (--again reruns)")
            continue
        pred.parent.mkdir(parents=True, exist_ok=True)
        print(f"\n=== {arm} seed {seed} ===\n" + " ".join(cmd[1:]), flush=True)
        subprocess.run(cmd, cwd=REPO, check=True)
    print("\nNext: report")


# ── 4. report ─────────────────────────────────────────────────────────────────

def compare(base: list[dict], mine: list[dict]) -> Optional[dict]:
    """Paired numbers for one seed on one set of reels (same reels, same order by code)."""
    b = {p["code"]: p for p in base}
    m = {p["code"]: p for p in mine}
    codes = sorted(b.keys() & m.keys())
    if not codes:
        return None
    parser = [b[c]["parser_hit"] for c in codes]
    bh, mh = [b[c]["ml_hit"] for c in codes], [m[c]["ml_hit"] for c in codes]
    return {"n": len(codes), "parser": sum(parser), "base": sum(bh), "mine": sum(mh),
            "ci": ev.paired_delta_interval(bh, mh), "p": ev.mcnemar_exact(bh, mh)[2],
            "ci_vs_parser": ev.paired_delta_interval(parser, mh)}


def verdict_line(rows: list[dict]) -> str:
    if not rows:
        return "no runs yet"
    gains = [r["mine"] - r["base"] for r in rows]
    if all(g > 0 for g in gains) and all(r["ci"] and r["ci"][0] > 0 for r in rows):
        return "BETTER: the new model wins on every seed and every interval is above zero"
    if all(g > 0 for g in gains):
        return "LIKELY BETTER: wins on every seed, but the interval still includes zero (label more held-out reels)"
    if all(g < 0 for g in gains):
        return "WORSE on every seed: keep the current model"
    return "NO CLEAR CHANGE: mixed across seeds; keep the current model"


def report(seeds: Iterable[int]) -> str:
    lines = ["# M2 with Nick's export: report", ""]
    for name, key in (("Frozen dev split", "dev"), ("Your held-out reels", "mine")):
        rows = []
        for s in seeds:
            base = [p for p in read_jsonl(OUT / "preds" / f"base_s{s}.jsonl") if p["set"] == key]
            mine = [p for p in read_jsonl(OUT / "preds" / f"mine_s{s}.jsonl") if p["set"] == key]
            r = compare(base, mine)
            if r:
                rows.append({**r, "seed": s})
        lines += [f"## {name}", ""]
        if not rows:
            lines += ["(no runs, or no labeled reels here yet)", ""]
            continue
        lines += ["| seed | reels | rules | M2 before | M2 + your data | change | 95% CI | p | new M2 vs rules |",
                  "|---|---|---|---|---|---|---|---|---|"]
        pct = lambda k, n: f"{100 * k / n:.1f}%"  # noqa: E731
        for r in rows:
            ci = f"[{r['ci'][0]:+.1%}, {r['ci'][1]:+.1%}]" if r["ci"] else "n/a"
            vp = r["ci_vs_parser"]
            lines.append(f"| {r['seed']} | {r['n']} | {pct(r['parser'], r['n'])} | {pct(r['base'], r['n'])} | "
                         f"{pct(r['mine'], r['n'])} | {(r['mine'] - r['base']) / r['n']:+.1%} | {ci} | {r['p']:.3g} | "
                         + (f"{(r['mine'] - r['parser']) / r['n']:+.1%} [{vp[0]:+.1%}, {vp[1]:+.1%}]" if vp else "n/a") + " |")
        lines += ["", "**" + verdict_line(rows) + "**", ""]
    lines += ["Test split: not read. Report it once, at the end, with `python -m src.ingestion.eval --final \"why\"`."]
    return "\n".join(lines)


def cmd_report(args) -> None:
    text = report(args.seeds)
    (OUT / "report.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    print(f"\nsaved {OUT / 'report.md'}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract", help="read the export into data/my_export/reels.jsonl")
    p.add_argument("export", help="the export .zip, or the unzipped folder")
    p = sub.add_parser("label", help="say which place each reel is about")
    p.add_argument("--heldout", action="store_true", help="label the held-out pool (scored, never trained on)")
    p.add_argument("--limit", type=int, default=0, help="stop after this many")
    for name in ("train", "report"):
        p = sub.add_parser(name)
        p.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
        if name == "train":
            p.add_argument("--again", action="store_true", help="rerun runs that already finished")
            p.add_argument("--force", action="store_true", help="train with under 30 labels")
    args = ap.parse_args(argv)
    {"extract": cmd_extract, "label": cmd_label, "train": cmd_train, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
