"""Burner job: who ELSE is on each post. Instagram collab co-authors (coauthor_producers),
accounts tagged in the video (usertags) and the paid-partnership tag (sponsor_tags). A collab
post by a creator AND a business ("nycfoodiegirll and sundaymorningcinnamonrolls") is close
to a giveaway that the business is the venue.

Order: dev reels -> train reels (corpus, then burner) -> profiles of co-authors / tagged
accounts / posters not looked up yet (account type + category). Test reels are skipped.
Pacing: at most 15 Instagram calls per 10 minutes. Stops for good on any challenge /
login-required / rate-limit error or 3 failures in a row. Resumable. Logs counts only.
Writes data/experiments/post_tags.jsonl and appends to data/experiments/accounts.jsonl.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import dev_fetch as D  # noqa: E402  (pacing, stop rules, account fetch, logging, .env)
from src.ingestion import eval as ev  # noqa: E402

OUT = D.EXP / "post_tags.jsonl"


def users(xs):
    out = []
    for u in xs or []:
        u = getattr(u, "user", u)                              # usertags wrap the user
        name = getattr(u, "username", None)
        if name:
            out.append(name)
    return out


def main():
    if "--wait" in sys.argv:
        D.log("waiting 10 min before the first call")
        D.time.sleep(D.WINDOW)
    rows = ev.load_labels()
    burner = [json.loads(l) for l in (D.WORK / "fixtures" / "labels.inbox.jsonl").read_text(encoding="utf-8").split("\n") if l.strip()]
    order = ([r for r in rows if ev.split_of(r) == "dev"] + [r for r in rows if ev.split_of(r) == "train"]
             + [r for r in burner if ev.split_of({"url": r["url"], "input": r.get("input") or {}}) != "test"])
    done = D.codes_in(OUT)
    todo = [r for r in {ev._code(r["url"]): r for r in order}.values() if ev._code(r["url"]) not in done]
    D.log(f"tags: {len(todo)} posts to read ({len(done)} done)")
    try:
        cl = D.w._client()
    except Exception as exc:  # noqa: BLE001
        D.log(f"LOGIN FAILED, stopping: {type(exc).__name__}: {exc}")
        sys.exit(1)
    have = D.known_accounts()
    fails = 0
    for n, r in enumerate(todo, 1):
        code = ev._code(r["url"])
        try:
            D.pace()
            m = cl.media_info(cl.media_pk_from_code(code))
            fails = 0
        except Exception as exc:  # noqa: BLE001
            if D.must_stop(exc):
                D.log(f"STOPPING (no retry): {type(exc).__name__}")
                sys.exit(1)
            fails += 1
            D.log(f"post {n}: failed {type(exc).__name__} ({fails} in a row)")
            if fails >= 3:
                D.log("3 failures in a row, stopping (no retry)")
                sys.exit(1)
            continue
        row = {"code": code, "coauthors": users(getattr(m, "coauthor_producers", None)),
               "tagged": users(getattr(m, "usertags", None)), "sponsors": users(getattr(m, "sponsor_tags", None))}
        with OUT.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        # profile of every co-author / sponsor we haven't seen (they're the likeliest venues)
        for u in row["coauthors"] + row["sponsors"]:
            if u.lower() not in have:
                try:
                    D.fetch_account(cl, u)
                    have.add(u.lower())
                except Exception as exc:  # noqa: BLE001
                    if D.must_stop(exc):
                        D.log(f"STOPPING (no retry): {type(exc).__name__}")
                        sys.exit(1)
        if n % 15 == 0:
            D.log(f"tags: {n}/{len(todo)} posts")
    # finally, posters still missing a profile (the restart interrupted these)
    posters = [(r.get("input") or {}).get("handle", "").lstrip("@").lower() for r in order]
    rest = [a for a in dict.fromkeys(posters) if a and a not in have]
    D.log(f"profiles: {len(rest)} posters left")
    for n, a in enumerate(rest, 1):
        try:
            D.fetch_account(cl, a)
            have.add(a)
        except Exception as exc:  # noqa: BLE001
            if D.must_stop(exc):
                D.log(f"STOPPING (no retry): {type(exc).__name__}")
                sys.exit(1)
        if n % 15 == 0:
            D.log(f"profiles: {n}/{len(rest)}")
    D.log("all done")


if __name__ == "__main__":
    main()
