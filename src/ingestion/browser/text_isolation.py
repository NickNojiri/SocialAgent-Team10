"""Separates human-written text from page chrome.

Per-domain selector profiles map CSS selectors to semantic roles; unknown
layouts fall back to generic landmarks. Selectors are best-effort by nature —
platforms change markup without notice — so every profile degrades to the
generic fallback instead of erroring.
"""

import re

from playwright.async_api import Page

from src.ingestion.schemas.snapshot import TextComponent, TextRole

SELECTOR_PROFILES: dict[str, list[tuple[str, TextRole]]] = {
    "instagram.com": [
        ("article h1", TextRole.CAPTION),
        ("h1", TextRole.TITLE),
        # A real post location tag is /explore/locations/<numeric-id>/<slug>/.
        # `:not([href$="/locations/"])` drops IG's site-wide "Locations" footer
        # link (href exactly /explore/locations/), and _LOCATION_HREF_RE below
        # enforces the numeric id so no other chrome link slips through.
        ('a[href*="/explore/locations/"]:not([href$="/locations/"])', TextRole.LOCATION_TAG),
    ],
}

# The location-tag href must carry a numeric location id, else it's page chrome.
_LOCATION_HREF_RE = re.compile(r"/locations/\d+")

GENERIC_PROFILE: list[tuple[str, TextRole]] = [
    ("h1", TextRole.TITLE),
    ("article", TextRole.OTHER),
    ("main", TextRole.OTHER),
]

MAX_PER_SELECTOR = 5
MAX_TEXT_LEN = 4000


def profile_for(host: str) -> list[tuple[str, TextRole]]:
    host = (host or "").lower().removeprefix("www.")
    for domain, profile in SELECTOR_PROFILES.items():
        if host == domain or host.endswith("." + domain):
            return profile + GENERIC_PROFILE
    return GENERIC_PROFILE


async def isolate_text(page: Page, host: str) -> list[TextComponent]:
    components: list[TextComponent] = []
    seen_texts: set[str] = set()
    for selector, role in profile_for(host):
        try:
            elements = await page.query_selector_all(selector)
        except Exception:
            continue
        for element in elements[:MAX_PER_SELECTOR]:
            try:
                text = (await element.inner_text()).strip()
            except Exception:
                continue
            if role is TextRole.LOCATION_TAG:
                try:
                    href = (await element.get_attribute("href")) or ""
                except Exception:
                    href = ""
                if not _LOCATION_HREF_RE.search(href):
                    continue   # a chrome link ("Locations" footer), not a real place tag
            text = text[:MAX_TEXT_LEN]
            if text and text not in seen_texts:
                seen_texts.add(text)
                components.append(TextComponent(role=role, text=text, selector=selector))
    return components
