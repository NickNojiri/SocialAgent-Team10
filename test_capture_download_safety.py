"""Video download safety (Track A #3, THREAT_MODEL T3): every bad download is refused
with no file left behind. All transports are in-memory fakes; nothing touches a network."""
import os
import tempfile
from contextlib import contextmanager

import pytest

import src.ingestion.pipeline.transcriber as tr
from src.ingestion.browser import url_guard
from src.ingestion.config import IngestionSettings


@pytest.fixture
def serve(monkeypatch):
    """Point httpx.stream at a fake server and record every temp file the download makes."""
    made = []
    real_mkstemp = tempfile.mkstemp

    def tracking_mkstemp(*a, **kw):
        fd, path = real_mkstemp(*a, **kw)
        made.append(path)
        return fd, path

    monkeypatch.setattr(tr.tempfile, "mkstemp", tracking_mkstemp)

    def install(handler):
        transport = tr.httpx.MockTransport(handler)

        @contextmanager
        def fake_stream(method, url, **kw):
            with tr.httpx.Client(transport=transport, follow_redirects=kw.pop("follow_redirects", False)) as c:
                with c.stream(method, url) as resp:
                    yield resp

        monkeypatch.setattr(tr.httpx, "stream", fake_stream)
        return made

    return install


def _download(url="https://media.example/v.mp4"):
    return tr.Transcriber(IngestionSettings())._download(url)


def test_a_normal_video_downloads(serve):
    made = serve(lambda r: tr.httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"\0" * 1000))
    path = _download()
    assert path == made[0] and os.path.getsize(path) == 1000
    os.unlink(path)


def test_internal_url_is_refused_before_any_request(serve):
    seen = []
    made = serve(lambda r: seen.append(str(r.url)) or tr.httpx.Response(200))
    assert _download("http://169.254.169.254/latest/meta-data/") is None
    assert seen == [] and made == []


def test_redirect_to_internal_is_refused(serve):
    seen = []

    def handler(r):
        seen.append(str(r.url))
        return tr.httpx.Response(302, headers={"location": "http://10.0.0.5/secret"})

    made = serve(handler)
    assert _download() is None
    assert seen == ["https://media.example/v.mp4"] and made == []


def test_oversized_body_is_refused_and_removed(serve, monkeypatch):
    monkeypatch.setattr(tr, "_MAX_BYTES", 100)
    made = serve(lambda r: tr.httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"\0" * 5000))
    assert _download() is None
    assert all(not os.path.exists(p) for p in made)


def test_wrong_content_type_is_refused_with_no_file(serve):
    made = serve(lambda r: tr.httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"))
    assert _download() is None
    assert made == []


@pytest.mark.parametrize("url, blocked", [
    ("https://www.instagram.com/reel/ABC/", False),
    ("http://127.1/", True), ("http://2130706433/", True), ("http://0x7f.0.0.1/", True),
    ("http://[::ffff:127.0.0.1]/", True), ("http://192.168.1.1/", True), ("http://172.16.0.1/", True),
    ("http://[fc00::1]/", True), ("http://printer.local/", True), ("ftp://example.com/", True),
    ("file:///etc/passwd", True),
])
def test_blocked_reason_reads_every_internal_spelling(monkeypatch, url, blocked):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("157.240.1.1", 0))])
    assert bool(url_guard.blocked_reason(url)) is blocked
