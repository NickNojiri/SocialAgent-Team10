"""The phone app's Instagram import (mobile_app.py), run in a real browser on a fake export.

The app reads Instagram's "Download your information" zip on the phone and sends only
reel links. This drives the page in demo mode with a small export made here, so it needs
Chromium but no server, network or Instagram.
"""
import io
import json
import os
import zipfile

import pytest

from src.ingestion.serving import mobile_app

sync_api = pytest.importorskip("playwright.sync_api")


def _mojibake(text: str) -> str:
    """Instagram's export writes UTF-8 bytes as \\u00XX escapes."""
    return text.encode("utf-8").decode("latin-1")


def _export_zip() -> bytes:
    me, maya, jo = "Nick", _mojibake("Mayá"), "Jordan"
    thread = lambda friend, msgs: {"participants": [{"name": friend}, {"name": me}], "messages": msgs}  # noqa: E731
    share = lambda who, code, text: {"sender_name": who, "timestamp_ms": 1730000000000,  # noqa: E731
                                     "share": {"link": f"https://www.instagram.com/reel/{code}/", "share_text": text}}
    files = {
        "your_instagram_activity/messages/inbox/maya_1/message_1.json": thread(maya, [
            share(maya, "DEMOthw001", "Best dim sum in Irvine 🥟"),
            share(maya, "FOODAAAA02", "This ramen spot is unreal"),
            share(me, "MINEAAAA03", "omakase night"),
            {"sender_name": maya, "timestamp_ms": 1, "content": "private words that must not leave the phone"},
        ]),
        "your_instagram_activity/messages/inbox/jordan_2/message_1.json": thread(jo, [
            share(jo, "SKATEAAA04", "kickflip compilation"),
            share(jo, "NOTEXTAA05", ""),
            share(jo, "FOODAAAA02", "same ramen again"),
        ]),
        "your_instagram_activity/saved/saved_posts.json": {"saved_saved_media": [
            {"title": "someeats", "string_map_data": {"Saved on": {"href": "https://www.instagram.com/p/SAVEDAAA06/", "timestamp": 1}}},
        ]},
        "your_instagram_activity/media/photo.jpg": "not read",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body if isinstance(body, str) else json.dumps(body))
    return buf.getvalue()


@pytest.fixture(scope="module")
def page():
    with sync_api.sync_playwright() as p:
        exe = os.getenv("PW_CHROMIUM_PATH") or None
        try:
            browser = p.chromium.launch(executable_path=exe)
        except Exception as exc:                       # no browser installed here
            pytest.skip(f"Chromium not available: {exc}")
        pg = browser.new_page(viewport={"width": 390, "height": 844})
        sent = []
        pg.route("http://app.test/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body=mobile_app.APP_HTML)
            if route.request.url.rstrip("/").endswith("/app") else
            (sent.append(route.request.post_data), route.fulfill(status=404, body=""))[1])
        pg.goto("http://app.test/app")
        pg.sent = sent
        yield pg
        browser.close()


def test_first_run_opens_connect_with_the_download_checklist(page):
    assert page.get_by_role("heading", name="Get your reels in").is_visible()
    assert page.get_by_text("Messages", exact=True).is_visible()
    assert page.locator('a[href^="https://accountscenter.instagram.com/"]').count() >= 1
    assert "null" not in page.locator("main").inner_text()


def test_import_reads_the_zip_on_the_phone_and_keeps_food(page):
    page.locator('input[type="file"]').set_input_files(
        {"name": "instagram-export.zip", "mimeType": "application/zip", "buffer": _export_zip()})
    page.get_by_text("6 reels").wait_for(timeout=10000)
    summary = page.locator("section.block").nth(1).inner_text()
    assert "from 2 friends" in summary                 # Nick is "me", not a friend
    assert "4 look like food" in summary               # dim sum, ramen, omakase, someeats
    assert "1 we can't tell yet" in summary            # the share with no caption
    page.get_by_role("button", name="Add the 5 food reels").click()
    page.get_by_text("5 on the way").wait_for()
    assert page.sent == []                             # demo: nothing left the page


def test_cards_say_who_sent_them_and_unsure_spots_get_checked(page):
    page.get_by_role("button", name="See My Spots").click()
    assert page.get_by_text("💬 Mayá sent this").first.is_visible()   # the export's mojibake, fixed
    check = page.locator("section.check")
    assert "Is this the right place?" in check.inner_text() and "Katsu Bar" in check.inner_text()
    check.get_by_role("button", name="✓ Yes").click()
    assert page.locator("section.check").count() == 0


def test_only_links_are_kept_on_the_phone(page):
    stored = page.evaluate("localStorage.getItem('spotbot.import.v1')")
    assert "private words" not in stored and "ramen" not in stored
    assert set(json.loads(stored)) == {"DEMOthw001", "FOODAAAA02", "MINEAAAA03", "NOTEXTAA05", "SAVEDAAA06"}
