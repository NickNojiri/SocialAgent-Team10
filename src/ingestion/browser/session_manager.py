"""Playwright lifecycle and polite navigation for public pages.

Guardrails by construction:
- fresh, cookie-less context per URL — no sessions, no login automation
- per-domain throttle so no host is ever hammered
- login/consent walls are classified and reported, never bypassed
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    Page,
    Playwright,
    Response,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from src.ingestion.browser.text_isolation import isolate_text
from src.ingestion.config import IngestionSettings
from src.ingestion.schemas.results import FetchStatus
from src.ingestion.schemas.snapshot import PageSnapshot

# Markers that the platform is asking for an account instead of serving content.
_LOGIN_URL_HINTS = ("/accounts/login", "/login")
_LOGIN_FORM_SELECTOR = 'form input[type="password"]'
_CONSENT_HINTS = ("allow all cookies", "accept cookies", "before you continue")
_GONE_HINTS = ("page isn't available", "page not found", "content unavailable")


class SocialSessionManager:
    """Async context manager owning one headless Chromium for a batch of URLs."""

    def __init__(self, settings: Optional[IngestionSettings] = None):
        self.settings = settings or IngestionSettings()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._last_hit: dict[str, float] = {}

    async def __aenter__(self) -> "SocialSessionManager":
        self._playwright = await async_playwright().start()
        launch_kwargs: dict = {"headless": self.settings.headless}
        if self.settings.chromium_executable_path:
            launch_kwargs["executable_path"] = self.settings.chromium_executable_path
        self._browser = await self._playwright.chromium.launch(**launch_kwargs)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._browser is not None:
                await self._browser.close()
        finally:
            if self._playwright is not None:
                await self._playwright.stop()

    # ── public API ─────────────────────────────────────────────────────────

    async def fetch(self, url: str) -> PageSnapshot:
        """Navigate one public URL in a fresh context and capture what it served."""
        if self._browser is None:
            raise RuntimeError("SocialSessionManager must be entered with 'async with'")

        problem = self._reject_reason(url)
        if problem is not None:
            return self._snapshot(url, FetchStatus.ERROR, error=problem)

        host = urlparse(url).netloc.lower() or "local"
        await self._throttle(host)

        context = await self._browser.new_context(
            user_agent=self.settings.user_agent,
            viewport={
                "width": self.settings.viewport_width,
                "height": self.settings.viewport_height,
            },
            locale=self.settings.locale,
        )
        try:
            page = await context.new_page()
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.settings.nav_timeout_ms,
                )
            except PlaywrightTimeoutError:
                return self._snapshot(url, FetchStatus.TIMEOUT)
            except PlaywrightError as exc:
                return self._snapshot(url, FetchStatus.ERROR, error=str(exc))

            await self._settle(page)
            await self._dismiss_login_overlay(page)

            meta = await self._collect_meta(page)
            status = await self._classify(page, response, meta)
            html = await page.content()
            jsonld = await self._collect_jsonld(page)
            components = (
                await isolate_text(page, urlparse(page.url).netloc)
                if status is FetchStatus.OK
                else []
            )

            return self._snapshot(
                url,
                status,
                final_url=page.url,
                html=html,
                meta=meta,
                jsonld=jsonld,
                components=components,
            )
        finally:
            await context.close()

    # ── internals ──────────────────────────────────────────────────────────

    def _reject_reason(self, url: str) -> Optional[str]:
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return None
        if parsed.scheme == "file" and self.settings.allow_file_urls:
            return None
        return f"unsupported URL (public http(s) only): {url!r}"

    async def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.settings.per_domain_delay_s - (time.monotonic() - last)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_hit[host] = time.monotonic()

    async def _settle(self, page: Page) -> None:
        """Give dynamic layouts a bounded chance to render; never wait forever."""
        try:
            await page.wait_for_load_state(
                "networkidle", timeout=self.settings.settle_timeout_ms
            )
        except PlaywrightTimeoutError:
            pass  # busy pages never go idle; whatever has rendered is what we read

    async def _dismiss_login_overlay(self, page: Page) -> None:
        """Best-effort: close the "log in to continue" dialog IG overlays on public
        posts, so the content underneath becomes readable. We never type credentials
        or follow the login flow — we only click the dialog's own dismiss control.
        A miss is fine; the og: tags are read regardless of the overlay."""
        for selector in (
            'div[role="dialog"] svg[aria-label="Close"]',
            'div[role="dialog"] [aria-label="Close"]',
            'svg[aria-label="Close"]',
            'div[role="dialog"] button:has-text("Not Now")',
            'div[role="dialog"] button:has-text("Not now")',
        ):
            try:
                element = await page.query_selector(selector)
                if element is not None and await element.is_visible():
                    await element.click(timeout=1500)
                    await page.wait_for_timeout(300)  # let the dialog animate out
                    return
            except PlaywrightError:
                continue  # selector absent or not clickable — try the next one

    async def _classify(
        self, page: Page, response: Optional[Response], meta: dict[str, str]
    ) -> FetchStatus:
        if response is not None and response.status == 404:
            return FetchStatus.NOT_FOUND

        final_url = (page.url or "").lower()
        if any(hint in final_url for hint in _LOGIN_URL_HINTS):
            return FetchStatus.LOGIN_WALL

        try:
            body_text = ((await page.text_content("body")) or "").lower()[:5000]
        except PlaywrightError:
            body_text = ""

        if any(hint in body_text for hint in _GONE_HINTS):
            return FetchStatus.NOT_FOUND

        # A password form only counts as a wall when the page gave us no public
        # caption excerpt — platforms often overlay a login prompt on top of
        # content that is still present in the og: tags.
        if not meta.get("og:description"):
            try:
                if await page.query_selector(_LOGIN_FORM_SELECTOR):
                    return FetchStatus.LOGIN_WALL
            except PlaywrightError:
                pass

        # Cookie dialogs only count as a wall when they're all the page gives us.
        try:
            dialog = await page.query_selector('[role="dialog"]')
            if dialog is not None:
                dialog_text = ((await dialog.inner_text()) or "").lower()
                if any(hint in dialog_text for hint in _CONSENT_HINTS):
                    if await page.query_selector("article, main") is None:
                        return FetchStatus.CONSENT_WALL
        except PlaywrightError:
            pass

        return FetchStatus.OK

    async def _collect_meta(self, page: Page) -> dict[str, str]:
        try:
            pairs = await page.eval_on_selector_all(
                "meta[name], meta[property]",
                "els => els.map(e => [e.getAttribute('property') || e.getAttribute('name'),"
                " e.getAttribute('content') || ''])",
            )
        except PlaywrightError:
            return {}
        return {key: value for key, value in pairs if key and value}

    async def _collect_jsonld(self, page: Page) -> list:
        try:
            blocks = await page.eval_on_selector_all(
                'script[type="application/ld+json"]',
                "els => els.map(e => e.textContent || '')",
            )
        except PlaywrightError:
            return []
        parsed: list = []
        for block in blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            parsed.extend(data if isinstance(data, list) else [data])
        return parsed

    def _snapshot(self, url: str, status: FetchStatus, **extra) -> PageSnapshot:
        return PageSnapshot(
            url=url,
            status=status,
            fetched_at=datetime.now(timezone.utc),
            **extra,
        )
