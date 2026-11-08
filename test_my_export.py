"""experiments/burner_study/my_export.py: training M2 on Nick's own export, without leaks."""
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "my_export", Path(__file__).parent / "experiments" / "burner_study" / "my_export.py")
mx = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mx
_spec.loader.exec_module(mx)

ME, FRIEND = "Nicholas Nojiri", "Maya"
CSS = "<style>.x{color:#fff}</style>"


def _html_thread(*messages):
    """The HTML export's layout: <h2> sender, then one <div> per line, the link in an <a>."""
    body = "".join(f'<div class="pam _a6-g uiBoxWhite noborder"><h2>{who}</h2><div><div></div>{inner}</div>'
                   f'<div class="_a6-o">Mar 04, 2026 10:42 pm</div></div>' for who, inner in messages)
    return f"<html><head>{CSS}<title>t</title></head><body><main>{body}</main></body></html>"


def _share_html(caption, handle, code):
    return (f"<div>{'You' if False else 'Someone'} sent an attachment.</div><div><div><div>{caption}</div>"
            f"<div>{handle}</div><div><a href=\"https://www.instagram.com/reel/{code}/?id=1_2\">"
            f"https://www.instagram.com/reel/{code}/?id=1_2</a></div></div></div>")


def _json_thread(friend, msgs):
    return json.dumps({"participants": [{"name": friend}, {"name": ME}], "messages": msgs})


def _share_json(who, code, text, owner="eatsbyjo"):
    moj = lambda s: s.encode("utf-8").decode("latin-1")  # noqa: E731
    return {"sender_name": moj(who), "timestamp_ms": 1,
            "share": {"link": f"https://www.instagram.com/reel/{code}/", "share_text": moj(text),
                      "original_content_owner": owner}}


def test_html_shares_keep_caption_and_poster_but_not_typed_words():
    html = _html_thread(
        (ME, _share_html("Best burritos<br>only on weekends &#064;thecut", "waydirtydiet", "DVebfLoEj7l")),
        (FRIEND, "<div>private plans for friday https://www.instagram.com/reel/TYPEDLINK1/</div>"
                 '<div><a href="https://www.instagram.com/reel/TYPEDLINK1/">link</a></div>'),
    )
    got = list(mx.shares_from_html(html))
    assert got[0] == {"code": "DVebfLoEj7l", "caption": "Best burritos\nonly on weekends @thecut",
                      "handle": "waydirtydiet", "sender": ME, "ts": 0}
    assert got[1]["code"] == "TYPEDLINK1" and got[1]["caption"] == "" and got[1]["handle"] == ""


def test_json_shares_fix_mojibake_and_skip_message_text():
    text = _json_thread(FRIEND, [
        _share_json("Mayá", "FOODAAAA02", "ramen 🍜 at Kotoya"),
        {"sender_name": FRIEND, "timestamp_ms": 2, "content": "private words https://www.instagram.com/reel/NOPE12345/"},
    ])
    got = list(mx.shares_from_json(text))
    assert [(s["code"], s["caption"], s["handle"], s["sender"]) for s in got] == \
        [("FOODAAAA02", "ramen 🍜 at Kotoya", "eatsbyjo", "Mayá")]


def test_read_export_from_zip_dedupes_and_never_keeps_names(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("your_instagram_activity/messages/inbox/maya_1/message_1.json", _json_thread(FRIEND, [
            _share_json(FRIEND, "SAMEREEL01", "short"), _share_json(ME, "MINEONLY01", "mine")]))
        z.writestr("your_instagram_activity/messages/inbox/jo_2/message_1.html", _html_thread(
            (ME, _share_html("a much longer caption here", "poster.one", "SAMEREEL01"))))
        z.writestr("your_instagram_activity/messages/inbox/al_3/message_1.json", _json_thread("Al", [
            _share_json(ME, "MINEAGAIN1", "x")]))
        z.writestr("your_instagram_activity/saved/saved_posts.json", "{}")
    path = tmp_path / "export.zip"
    path.write_bytes(buf.getvalue())
    reels = {r["code"]: r for r in mx.read_export(path)}
    assert set(reels) == {"SAMEREEL01", "MINEONLY01", "MINEAGAIN1"}
    assert reels["SAMEREEL01"] == {"code": "SAMEREEL01", "caption": "a much longer caption here",
                                   "handle": "poster.one", "shared_by": "friend"}
    assert reels["MINEONLY01"]["shared_by"] == "me"
    assert FRIEND not in json.dumps(list(reels.values()))


def test_split_drops_known_reels_and_test_creators(monkeypatch):
    pools = {"tr": "train", "dv": "dev", "ts": "test"}
    monkeypatch.setattr(mx, "pool_of", lambda row: pools[row["input"]["handle"]])
    reels = [{"code": c, "caption": "", "handle": h, "shared_by": "me"}
             for c, h in (("KNOWN00001", "tr"), ("NEWTRAIN01", "tr"), ("NEWDEV0001", "dv"), ("NEWTEST001", "ts"))]
    train, held, n = mx.split_reels(reels, {"KNOWN00001"})
    assert [r["url"] for r in train] == ["https://www.instagram.com/reel/NEWTRAIN01/"]
    assert [r["pool"] for r in held] == ["dev"]
    assert n == {"already in the project": 1, "train": 1, "dev": 1, "test": 1}


def test_a_silver_mark_cannot_move_a_dev_creator_into_training():
    splits = json.loads(mx.ev.SPLITS_PATH.read_text(encoding="utf-8"))["creators"]
    dev_creator = next(c for c, s in splits.items() if s == "dev")
    row = {"url": "https://www.instagram.com/reel/BRANDNEW01/", "input": {"handle": dev_creator}, "source": "silver"}
    assert mx.ev.split_of(row) == "train"         # the project rule: silver is always train
    assert mx.pool_of(row) == "dev"               # but the export's reels follow their creator


def _setup_out(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(mx, "OUT", tmp_path)
    mx.write_jsonl(tmp_path / "reels.jsonl", rows)


def test_label_writes_gold_rows_and_resumes(tmp_path, monkeypatch):
    rows = [{**mx.to_row({"code": f"LABELME00{i}", "caption": "Dinner at @kotoyaramen 📍 Kotoya Ramen, LA",
                          "handle": "eatsbyjo", "shared_by": "friend"}), "pool": "train"} for i in range(3)]
    rows.append({**mx.to_row({"code": "HELDOUT001", "caption": "x", "handle": "h", "shared_by": "me"}), "pool": "dev"})
    _setup_out(tmp_path, monkeypatch, rows)
    shown, answers = [], iter(["9", "1", "0", "q"])
    n = mx.cmd_label(SimpleNamespace(heldout=False, limit=0), ask=lambda _: next(answers), say=shown.append)
    assert n == 2
    labels = mx.read_jsonl(tmp_path / "labels.jsonl")
    assert labels[0]["gold"]["venue"] and labels[0]["gold"]["in_catalog"] is True
    assert labels[1]["gold"] == {"venue": None, "city": None, "category": None, "in_catalog": False}
    assert all(r["labeler"] == "nick" and r["verdict"]["venue"] in ("right", "wrong", "missing") for r in labels)
    assert any("a number from the list" in s for s in shown)          # "9" was out of range
    assert not any("parser" in s.lower() or "rules" in s.lower() for s in shown[1:])   # no hint which is "right"
    answers = iter(["Kotoya", "q"])
    assert mx.cmd_label(SimpleNamespace(heldout=False, limit=0), ask=lambda _: next(answers), say=shown.append) == 1
    assert len(mx.read_jsonl(tmp_path / "labels.jsonl")) == 3
    assert not (tmp_path / "labels.heldout.jsonl").exists()


def test_labeled_row_verdicts():
    row = mx.to_row({"code": "C0000001", "caption": "", "handle": "", "shared_by": "me"})
    assert mx.labeled_row(row, "Kotoya Ramen", "Kotoya ramen")["verdict"]["venue"] == "right"
    assert mx.labeled_row(row, "Kotoya Ramen", None)["verdict"]["venue"] == "missing"
    assert mx.labeled_row(row, "Kotoya Ramen", "Los Angeles")["verdict"]["venue"] == "wrong"
    assert mx.labeled_row(row, None, None)["verdict"]["venue"] == "right"


def test_load_extra_keeps_only_safe_scoreable_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "pool_of", lambda row: row["pool"])
    row = lambda code, pool, venue="Kotoya", verdict="right": {  # noqa: E731
        "url": f"https://www.instagram.com/reel/{code}/", "input": {}, "pool": pool, "source": "my_export",
        "gold": {"venue": venue, "in_catalog": venue is not None}, "verdict": {"venue": verdict}}
    mx.write_jsonl(tmp_path / "l.jsonl", [row("OK0001", "train"), row("USED01", "train"), row("DEV001", "dev"),
                                          row("NOTSCORE", "train", verdict="needs_review"), row("NONE01", "train", None)])
    got = mx.load_extra(tmp_path / "l.jsonl", "train", {"USED01"})
    assert [mx.ev._code(r["url"]) for r in got] == ["OK0001", "NONE01"]
    assert all("source" not in r for r in got)                         # gold: full weight, never "silver"
    assert [mx.ev._code(r["url"]) for r in mx.load_extra(tmp_path / "l.jsonl", "dev", set())] == ["DEV001"]


def test_both_arms_share_everything_but_the_extra_data(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "OUT", tmp_path)
    (tmp_path / "labels.heldout.jsonl").write_text("", encoding="utf-8")
    (base_arm, s1, base), (mine_arm, s2, mine) = mx.runs([13])
    assert (base_arm, mine_arm, s1, s2) == ("base", "mine", 13, 13)
    assert "--extra" not in base and "--extra" in mine
    strip = lambda cmd: [a for a in cmd if "my_" not in a and "labels.jsonl" not in a and not a.endswith("_s13.jsonl")]  # noqa: E731
    assert strip(base) == [a for a in strip(mine) if a != "--extra"]
    assert "--extra-dev" in base and "--epochs" in base and "--parser-pick" in base


def _preds(path, hits, set_="dev", parser=None):
    mx.write_jsonl(path, [{"set": set_, "code": f"c{i}", "parser_hit": (parser or hits)[i], "ml_hit": h}
                          for i, h in enumerate(hits)])


def test_report_says_better_only_when_every_seed_agrees(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "OUT", tmp_path)
    (tmp_path / "preds").mkdir()
    n = 200
    for s in (13, 14):
        _preds(tmp_path / "preds" / f"base_s{s}.jsonl", [i < 120 for i in range(n)], parser=[i < 100 for i in range(n)])
        _preds(tmp_path / "preds" / f"mine_s{s}.jsonl", [i < 150 for i in range(n)])
    text = mx.report([13, 14])
    assert "BETTER: the new model wins on every seed" in text and "| 13 | 200 | 50.0% | 60.0% | 75.0% | +15.0%" in text
    assert "(no runs, or no labeled reels here yet)" in text          # no held-out preds
    _preds(tmp_path / "preds" / "mine_s14.jsonl", [i < 110 for i in range(n)])
    assert "NO CLEAR CHANGE" in mx.report([13, 14])
    assert mx.verdict_line([]) == "no runs yet"


def test_train_refuses_with_too_few_labels(tmp_path, monkeypatch):
    monkeypatch.setattr(mx, "OUT", tmp_path)
    with pytest.raises(SystemExit, match="label at least 30"):
        mx.cmd_train(SimpleNamespace(seeds=[13], again=False, force=False))
