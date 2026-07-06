"""On-screen text from the reel's cover image (Phase 2.6).

Some reels carry their whole pitch as burned-in text — venue name, address,
dates typed over the video — never spoken and never in the caption. The cover
image (og:image / the authed thumbnail) almost always shows that text, so
OCRing one image is the cheap 80/20; sampling and OCRing video *frames* is the
noted future upgrade (far slower — see docs/PRODUCT_ROADMAP.md).

Design matches the transcriber exactly: injectable backend for offline tests,
lazy import of the heavy dependency (rapidocr-onnxruntime — pure pip, no system
binary), opt-in via settings, and every failure degrades to "no text" — OCR
never breaks a capture.
"""

import logging
from typing import Callable, Optional

import httpx

from src.ingestion.config import IngestionSettings

log = logging.getLogger("ingestion.ocr")

# An OCR backend maps raw image bytes to the text it sees (or None).
OcrFn = Callable[[bytes], Optional[str]]

_DOWNLOAD_TIMEOUT = 15.0
_MAX_BYTES = 10 * 1024 * 1024   # covers are small; refuse anything huge
_MAX_CHARS = 500                # cap what we feed the LLM
_MIN_SCORE = 0.6                # drop low-confidence junk lines


class ImageTextReader:
    def __init__(self, settings: Optional[IngestionSettings] = None, ocr_fn: Optional[OcrFn] = None):
        self.settings = settings or IngestionSettings()
        self._ocr_fn = ocr_fn
        self._backend_failed = False   # sticky: a missing backend is checked once

    def read_url(self, image_url: Optional[str]) -> Optional[str]:
        """Download the cover image and return the on-screen text, or None."""
        if not image_url or not image_url.startswith("http"):
            return None
        data = self._download(image_url)
        if data is None:
            return None
        try:
            fn = self._ocr_fn or self._default_backend()
            if fn is None:
                return None
            text = fn(data)
        except Exception as exc:   # backend crash is a normal no-text outcome
            log.warning(f"[ocr] backend failed ({type(exc).__name__}: {exc})")
            return None
        cleaned = " ".join((text or "").split())[:_MAX_CHARS]
        if cleaned:
            log.info(f"[ocr] read {len(cleaned)} chars of on-screen text")
        return cleaned or None

    # ── internals ────────────────────────────────────────────────────────────

    def _download(self, image_url: str) -> Optional[bytes]:
        try:
            resp = httpx.get(image_url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            if len(resp.content) > _MAX_BYTES:
                log.warning(f"[ocr] image exceeded {_MAX_BYTES} bytes; skipping")
                return None
            return resp.content
        except Exception as exc:
            log.warning(f"[ocr] image download failed ({type(exc).__name__}: {exc})")
            return None

    def _default_backend(self) -> Optional[OcrFn]:
        if self._backend_failed:
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR   # lazy heavy/optional dep

            engine = RapidOCR()

            def _run(data: bytes) -> Optional[str]:
                result, _elapse = engine(data)
                if not result:
                    return None
                lines = []
                for entry in result:
                    if len(entry) >= 3 and float(entry[2]) < _MIN_SCORE:
                        continue
                    if len(entry) >= 2 and str(entry[1]).strip():
                        lines.append(str(entry[1]).strip())
                return " ".join(lines) or None

            return _run
        except Exception as exc:
            self._backend_failed = True
            log.warning(f"[ocr] no OCR backend (pip install rapidocr-onnxruntime): {exc}")
            return None
