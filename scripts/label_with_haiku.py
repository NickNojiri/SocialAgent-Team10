"""Second-opinion labeller: run each captured reel's text through Claude Haiku
and record what it extracts, so the human labelling pass has an independent read
to cross-check against (agree → trust, disagree → needs_review).

    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...          # the account with the credits
    python scripts/label_with_haiku.py --in fixtures/labels.batch2.jsonl \
                                       --out fixtures/labels.haiku.jsonl

Cost: ~$0.002/row (123 rows ≈ $0.25). Nothing is sent but caption / og_title /
hashtags / the IG location tag — never a token, never the video.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import anthropic

MODEL = "claude-haiku-4-5"
CATEGORIES = ("food_drink", "cafe_dessert", "nightlife", "live_music",
              "market_popup", "outdoors", "community", "other")

SYSTEM = (
    "You extract structured fields from one Instagram food/venue post. You are a "
    "strict extractor, not a chatbot. Output ONLY a JSON object, no prose.\n\n"
    "Keys:\n"
    '  "venue": the single specific named place the post is about (a restaurant, '
    "bar, café, stall, pop-up), verbatim as written — NOT a city, neighborhood, "
    "mall, theme park, or the poster's own handle unless the poster IS the venue. "
    "null if no specific venue is named.\n"
    '  "city": the city/neighborhood the venue is in, if stated or clearly implied; else null.\n'
    f'  "category": one of {", ".join(CATEGORIES)}.\n'
    '  "in_catalog": false if this is a recipe, product ad, meme, or general '
    "travel/tourism post rather than a specific place worth saving; else true.\n"
    '  "confidence": 0.0-1.0, how sure you are about "venue".\n'
    '  "note": <=12 words on any ambiguity (multiple venues named, spoken-only, collab, etc.).'
)

_JSON = re.compile(r"\{.*\}", re.DOTALL)


def ask(client: anthropic.Anthropic, inp: dict) -> dict:
    payload = {
        "caption": inp.get("caption") or "",
        "og_title": inp.get("og_title") or "",
        "handle": inp.get("handle") or "",
        "hashtags": inp.get("hashtags") or [],
        "ig_location_tag": inp.get("venue_candidate") or inp.get("location_text") or "",
    }
    resp = client.messages.create(
        model=MODEL,
        max_tokens=300,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    m = _JSON.search(text)
    data = json.loads(m.group(0)) if m else {}
    return {
        "venue": data.get("venue"),
        "city": data.get("city"),
        "category": data.get("category") if data.get("category") in CATEGORIES else "other",
        "in_catalog": bool(data.get("in_catalog", True)),
        "confidence": data.get("confidence"),
        "note": data.get("note") or "",
        "_usage": {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens,
                   "cache_read": getattr(resp.usage, "cache_read_input_tokens", 0)},
    }


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("fixtures/labels.haiku.jsonl"))
    ap.add_argument("--sleep", type=float, default=0.4)
    args = ap.parse_args()

    key = __import__("os").getenv("ANTHROPIC_API_KEY", "")
    if not key or key.endswith("...") or len(key) < 40:
        sys.exit("ANTHROPIC_API_KEY is missing or a placeholder — export your real key first "
                 "(console.anthropic.com → API keys).")
    client = anthropic.Anthropic()
    rows = [json.loads(l) for l in args.src.read_text().split("\n") if l.strip()]
    agree = tot = tin = tout = 0
    out = []
    for i, r in enumerate(rows, 1):
        if not r.get("input", {}).get("caption"):
            out.append(r)
            continue
        try:
            h = ask(client, r["input"])
        except anthropic.AuthenticationError as exc:
            sys.exit(f"auth failed — check ANTHROPIC_API_KEY: {exc}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{i}/{len(rows)}] {exc!r}", file=sys.stderr)
            time.sleep(2)
            continue
        u = h.pop("_usage")
        tin += u["in"]; tout += u["out"]
        r["haiku"] = h
        pred = (r.get("predicted") or {}).get("venue")
        tot += 1
        match = _norm(pred) == _norm(h["venue"])
        agree += match
        out.append(r)
        print(f"  [{i}/{len(rows)}] pred={pred!r}  haiku={h['venue']!r}  "
              f"{'=' if match else 'x'}  conf={h['confidence']} incat={h['in_catalog']}",
              file=sys.stderr)
        time.sleep(args.sleep)

    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in out) + "\n")
    cost = tin / 1e6 * 1.0 + tout / 1e6 * 5.0
    print(f"\n{tot} rows · haiku agrees with the heuristic on {agree} "
          f"({100*agree/tot:.0f}%) · ~${cost:.2f} ({tin} in / {tout} out) -> {args.out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
