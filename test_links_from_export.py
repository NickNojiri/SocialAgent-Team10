"""scripts/links_from_export.py — offline, synthetic export files only."""

import json
import zipfile

from scripts.links_from_export import _message_files, codes_from_json_texts

_THREAD = {"messages": [
    {"share": {"link": "https://www.instagram.com/reel/DdeX2SesDSF/?igsh=abc"}},
    {"content": "look https://instagram.com/p/AbC_12-x/ lol"},
    {"share": {"link": "https://www.instagram.com/reel/DdeX2SesDSF/"}},   # duplicate
    {"content": "no link here"},
]}


def test_codes_found_anywhere_and_deduplicated():
    assert codes_from_json_texts([json.dumps(_THREAD), "not json"]) == {"DdeX2SesDSF", "AbC_12-x"}


def test_reads_folder_and_zip(tmp_path):
    folder = tmp_path / "export" / "your_instagram_activity" / "messages" / "inbox" / "t1"
    folder.mkdir(parents=True)
    (folder / "message_1.json").write_text(json.dumps(_THREAD), encoding="utf-8")
    (tmp_path / "export" / "profile.json").write_text('{"x": "instagram.com/reel/NOTDM/"}', encoding="utf-8")
    assert codes_from_json_texts(_message_files(tmp_path / "export")) == {"DdeX2SesDSF", "AbC_12-x"}

    zpath = tmp_path / "export.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("messages/inbox/t1/message_1.json", json.dumps(_THREAD))
    assert codes_from_json_texts(_message_files(zpath)) == {"DdeX2SesDSF", "AbC_12-x"}
