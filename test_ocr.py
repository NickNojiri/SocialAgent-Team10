"""Offline tests for on-screen text OCR (Phase 2.6) — fake backend, no network."""

from src.ingestion.config import IngestionSettings
from src.ingestion.pipeline.ocr import ImageTextReader

SETTINGS = IngestionSettings(ocr_enabled=True)
ON_SCREEN = "CASA LOMA · 4th St Long Beach · FRIDAY 8PM"


class FakeDownloadReader(ImageTextReader):
    """Bypass the network: pretend the image downloaded fine (or not)."""

    def __init__(self, *args, data=b"fake-image-bytes", **kwargs):
        super().__init__(*args, **kwargs)
        self._data = data

    def _download(self, image_url):
        return self._data


class TestImageTextReader:
    def test_reads_text_via_injected_backend(self):
        reader = FakeDownloadReader(SETTINGS, ocr_fn=lambda data: ON_SCREEN)
        assert reader.read_url("https://cdn.ig/cover.jpg") == ON_SCREEN

    def test_no_image_url_is_no_text(self):
        reader = FakeDownloadReader(SETTINGS, ocr_fn=lambda data: ON_SCREEN)
        assert reader.read_url(None) is None
        assert reader.read_url("") is None
        assert reader.read_url("blob:notaurl") is None

    def test_download_failure_degrades_to_none(self):
        reader = FakeDownloadReader(SETTINGS, ocr_fn=lambda data: ON_SCREEN, data=None)
        assert reader.read_url("https://cdn.ig/cover.jpg") is None

    def test_backend_crash_degrades_to_none(self):
        def boom(data):
            raise RuntimeError("onnx exploded")

        reader = FakeDownloadReader(SETTINGS, ocr_fn=boom)
        assert reader.read_url("https://cdn.ig/cover.jpg") is None

    def test_empty_and_whitespace_results_are_none(self):
        assert FakeDownloadReader(SETTINGS, ocr_fn=lambda d: "   ").read_url("https://x/i.jpg") is None
        assert FakeDownloadReader(SETTINGS, ocr_fn=lambda d: None).read_url("https://x/i.jpg") is None

    def test_text_is_collapsed_and_capped(self):
        noisy = "  CASA\n\nLOMA   " + "x" * 900
        got = FakeDownloadReader(SETTINGS, ocr_fn=lambda d: noisy).read_url("https://x/i.jpg")
        assert got.startswith("CASA LOMA")
        assert len(got) <= 500

    def test_missing_backend_is_sticky_no_text(self):
        reader = FakeDownloadReader(SETTINGS)          # no injected fn
        reader._backend_failed = True                  # simulate rapidocr not installed
        assert reader.read_url("https://x/i.jpg") is None
