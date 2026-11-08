"""The accuracy report, rebuilt with one command (Track B #17).

    python scripts/eval_report.py                    # dev split -> docs/results/eval-report-dev.html
    python scripts/eval_report.py --split train      # while tuning
    python scripts/eval_report.py --out report.html

One self-contained HTML page: every metric with its 95% interval and count, a chart of
them, category accuracy per kind with the confusion table, and the error tables (each
missed venue and category, linked to its reel). Offline: the rules-only extractor, the
train-only ("fit") alias table, same as `python -m src.ingestion.eval --offline`.

The test split is not offered here. It's read once, at the end, with
`python -m src.ingestion.eval --split test --final "why"`, which logs the read.
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.ingestion import eval as ev  # noqa: E402
from src.ingestion.config import IngestionSettings  # noqa: E402
from src.ingestion.pipeline.normalizer import normalize  # noqa: E402

ESC = html.escape


def metrics(t: ev.Tally) -> list[tuple[str, int, int, str]]:
    return [
        ("Venue: same place", t.venue_same, t.venue_scored, "LABELING_GUIDE §3 match"),
        ("Venue: exact", t.venue_exact, t.venue_scored, "identical name"),
        ("Category", t.cat_hit, t.cat_scored, ""),
        ("City in place text", t.city_hit, t.city_scored, ""),
        ("Promo rejected (recall)", t.vague_hit, t.vague_scored, "gold says not a place"),
        ("Promo rejected (precision)", t.vague_hit, t.vague_hit + t.vague_fp, f"{t.vague_fp} real places rejected"),
    ]


def categories(rows: list[dict]) -> tuple[Counter, list[dict]]:
    """(gold, predicted) category pairs, and the misses."""
    pairs, misses = Counter(), []
    for row in rows:
        gold, verdict = row.get("gold") or {}, row.get("verdict") or {}
        if not (gold.get("category") and verdict.get("category") in ("right", "wrong", "missing")):
            continue
        cand = normalize(ev.raw_from_input(row["url"], row.get("input") or {}))
        pred = getattr(cand.get("category"), "value", cand.get("category")) or "none"
        pairs[(gold["category"], pred)] += 1
        if pred != gold["category"]:
            misses.append({"code": ev._code(row["url"]), "url": row["url"], "gold": gold["category"], "pred": pred,
                           "caption": ((row.get("input") or {}).get("caption") or "")[:90]})
    return pairs, misses


def chart(ms: list[tuple[str, int, int, str]]) -> str:
    """Horizontal bars with the 95% interval drawn as a whisker."""
    w, row_h, left = 640, 34, 210
    out = [f'<svg viewBox="0 0 {w} {row_h * len(ms) + 24}" role="img" aria-label="Accuracy with 95% intervals">']
    span = w - left - 70
    for i, (name, k, n, _) in enumerate(ms):
        y = 10 + i * row_h
        out.append(f'<text x="{left - 10}" y="{y + 15}" text-anchor="end">{ESC(name)}</text>')
        out.append(f'<rect x="{left}" y="{y + 4}" width="{span}" height="14" rx="3" class="track"/>')
        if n:
            lo, hi = ev.wilson_interval(k, n)
            out.append(f'<rect x="{left}" y="{y + 4}" width="{span * k / n:.1f}" height="14" rx="3" class="bar"/>')
            out.append(f'<line x1="{left + span * lo:.1f}" x2="{left + span * hi:.1f}" y1="{y + 11}" y2="{y + 11}" class="ci"/>')
            out.append(f'<text x="{left + span + 8}" y="{y + 15}">{100 * k / n:.1f}%</text>')
        else:
            out.append(f'<text x="{left + span + 8}" y="{y + 15}">n/a</text>')
    out.append("</svg>")
    return "".join(out)


def build(split: str) -> str:
    ev.use_aliases("fit")
    rows = [r for r in ev.load_labels() if ev.split_of(r) == split]
    t = ev.score(rows, use_llm=False, settings=IngestionSettings())
    ms = metrics(t)
    pairs, cat_misses = categories(rows)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=REPO).stdout.strip() or "?"
    golds = sorted({g for g, _ in pairs})
    preds = sorted({p for _, p in pairs} | set(golds))
    venue_misses = [r for r in t.rows if r["venue_same"] is False]

    def rate(k, n):
        if not n:
            return "<td>n/a</td><td></td><td>0</td>"
        lo, hi = ev.wilson_interval(k, n)
        return f"<td>{100 * k / n:.1f}%</td><td>[{100 * lo:.1f}%, {100 * hi:.1f}%]</td><td>{k} / {n}</td>"

    reel = lambda code: f'<a href="https://www.instagram.com/reel/{ESC(code)}/">{ESC(code)}</a>'  # noqa: E731
    p = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SpotBot accuracy report: {split}</title>
<style>
  :root {{ --ink:#1d2129; --mut:#5b6170; --line:#e3e6ec; --acc:#d6336c; --bg:#fff; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,"Segoe UI",sans-serif; font-variant-numeric:tabular-nums; }}
  main {{ max-width:900px; margin:0 auto; padding:28px 18px 60px; }}
  h1 {{ font-size:24px; margin:0 0 4px; text-wrap:balance; }} h2 {{ font-size:17px; margin:32px 0 10px; }}
  .sub {{ color:var(--mut); font-size:13px; }}
  table {{ border-collapse:collapse; width:100%; font-size:14px; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--mut); font-weight:600; font-size:12px; }}
  td.n {{ text-align:right; }} .hit {{ font-weight:700; }}
  svg {{ width:100%; height:auto; font-size:12px; }} svg text {{ fill:var(--ink); }}
  .track {{ fill:#eef0f4; }} .bar {{ fill:var(--acc); }} .ci {{ stroke:var(--ink); stroke-width:2; }}
  a {{ color:var(--acc); }} .wrap {{ overflow-x:auto; }}
</style></head><body><main>
<h1>SpotBot accuracy report: {split} split</h1>
<p class="sub">{date.today().isoformat()} · commit {ESC(commit)} · {t.n} reels · rules-only extractor, train-only alias table ·
rebuilt with <code>python scripts/eval_report.py --split {split}</code></p>
<h2>Every metric, with its 95% interval</h2>
{chart(ms)}
<div class="wrap"><table><tr><th>Metric</th><th>Rate</th><th>95% interval (Wilson)</th><th>Count</th><th>Note</th></tr>"""]
    for name, k, n, note in ms:
        p.append(f"<tr><td>{ESC(name)}</td>{rate(k, n)}<td>{ESC(note)}</td></tr>")
    p.append("</table></div>")
    p.append("<h2>Category, per kind</h2><div class=\"wrap\"><table><tr><th>Gold kind</th><th>Right</th><th>95% interval</th><th>Count</th></tr>")
    for g in golds:
        n = sum(c for (gg, _), c in pairs.items() if gg == g)
        p.append(f"<tr><td>{ESC(g)}</td>{rate(pairs[(g, g)], n)}</tr>")
    p.append("</table></div><h2>Category confusion (rows: gold, columns: predicted)</h2><div class=\"wrap\"><table><tr><th></th>")
    p.extend(f"<th>{ESC(c)}</th>" for c in preds)
    p.append("</tr>")
    for g in golds:
        cells = "".join(f'<td class="n{" hit" if c == g else ""}">{pairs[(g, c)] or ""}</td>' for c in preds)
        p.append(f"<tr><th>{ESC(g)}</th>{cells}</tr>")
    p.append(f"</table></div><h2>Venues missed ({len(venue_misses)})</h2><div class=\"wrap\"><table>"
             "<tr><th>Reel</th><th>Predicted</th><th>Gold</th><th>Rule that fired</th><th>Confidence</th></tr>")
    for r in venue_misses:
        p.append(f"<tr><td>{reel(r['code'])}</td><td>{ESC(str(r['pred_venue']))}</td><td>{ESC(str(r['gold_venue']))}</td>"
                 f"<td>{ESC(r['slot'])}</td><td class=\"n\">{r['conf']:.2f}</td></tr>")
    p.append(f"</table></div><h2>Categories missed ({len(cat_misses)})</h2><div class=\"wrap\"><table>"
             "<tr><th>Reel</th><th>Predicted</th><th>Gold</th><th>Caption starts</th></tr>")
    for m in cat_misses:
        p.append(f"<tr><td>{reel(m['code'])}</td><td>{ESC(m['pred'])}</td><td>{ESC(m['gold'])}</td><td>{ESC(m['caption'])}</td></tr>")
    p.append("</table></div><p class=\"sub\">The test split isn't in this report. It's read once, at the end, with "
             "<code>python -m src.ingestion.eval --split test --final \"why\"</code>, which logs the read in "
             "docs/results/TEST_LEDGER.md.</p></main></body></html>")
    return "\n".join(p)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", choices=["train", "dev"], default="dev")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    out = args.out or REPO / "docs" / "results" / f"eval-report-{args.split}.html"
    out.write_text(build(args.split), encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
