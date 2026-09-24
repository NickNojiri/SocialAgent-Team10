"""Accessibility checks for the three web pages (Track C #37).

Automated where a rule can be checked from the markup: WCAG AA text contrast
(4.5:1) computed from each page's own color tokens, alt text on images, a name
on every button and form field, color never the only signal, a language, and
new-tab links that say so. What needs a person (a screen-reader run) is listed
in docs/ACCESSIBILITY.md.
"""

import re

import pytest

import src.ingestion.serving.admin as admin

PAGES = {"catalog": admin._PAGE, "share": admin._SHARE_PAGE, "dash": admin._DASH_PAGE}

# (text color, background) pairs each page actually uses. A name is a :root
# token; a literal is a color written inline in that page's CSS.
PAIRS = {
    "catalog": [("txt", "bg"), ("txt", "card"), ("mut", "bg"), ("mut", "card"), ("acc", "bg"),
                ("acc", "card"), ("acc", "#26304a"), ("ok", "card"), ("bad", "bg"), ("bad", "card"),
                ("#06121f", "acc"), ("#0D1117", "acc")],
    "share": [("txt", "bg"), ("txt", "card"), ("mut", "bg"), ("mut", "card"), ("acc", "bg"),
              ("acc", "card"), ("acc", "#26304a"), ("ok", "card")],
    "dash": [("txt", "bg"), ("mut", "bg"), ("mut", "panel"), ("num", "panel"), ("acc", "bg"),
             ("acc", "panel"), ("ok", "panel"), ("bad", "panel"), ("warn", "panel")],
}


def _tokens(page: str) -> dict[str, str]:
    root = re.search(r":root\{(.*?)\}", page, re.S).group(1)
    return dict(re.findall(r"--([a-z]+):(#[0-9A-Fa-f]{3,6})", root))


def _luminance(color: str) -> float:
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_contrast_formula_matches_known_values():
    assert round(contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert round(contrast("#777777", "#FFFFFF"), 2) == 4.48      # the classic just-fails grey


@pytest.mark.parametrize("name, fg, bg", [(n, fg, bg) for n, pairs in PAIRS.items() for fg, bg in pairs])
def test_text_meets_wcag_aa_contrast(name, fg, bg):
    tokens = _tokens(PAGES[name])
    color = lambda c: c if c.startswith("#") else tokens[c]     # noqa: E731
    ratio = contrast(color(fg), color(bg))
    assert ratio >= 4.5, f"{name}: {fg} on {bg} is {ratio:.2f}:1, AA needs 4.5:1"


@pytest.mark.parametrize("name", PAGES)
def test_page_basics(name):
    page = PAGES[name]
    assert '<html lang="en">' in page and "<title>" in page and "<main" in page


@pytest.mark.parametrize("name", PAGES)
def test_every_image_has_alt_text(name):
    for tag in re.findall(r"<img\b[^>]*>", PAGES[name]):
        alt = re.search(r'alt="([^"]*)"', tag)
        assert alt and alt.group(1).strip(), f"{name}: image without alt text: {tag}"


def _visible_text(inner: str) -> str:
    inner = re.sub(r'<span[^>]*aria-hidden="true"[^>]*>.*?</span>', "", inner, flags=re.S)
    inner = re.sub(r"<[^>]+>", "", inner)
    return re.sub(r"\$\{[^}]*\}", "", inner)


@pytest.mark.parametrize("name", PAGES)
def test_every_button_has_a_name(name):
    for attrs, inner in re.findall(r"<button\b([^>]*)>(.*?)</button>", PAGES[name], re.S):
        named = "aria-label=" in attrs or re.search(r"[A-Za-z]", _visible_text(inner))
        assert named, f"{name}: a button a screen reader can't name: <button{attrs}>{inner}"


@pytest.mark.parametrize("name", PAGES)
def test_every_form_field_has_a_label(name):
    page = PAGES[name]
    labelled = set(re.findall(r'<label[^>]*for="([^"]+)"', page))
    for tag in re.findall(r"<(?:input|textarea)\b[^>]*>", page):
        field_id = re.search(r'id="([^"]+)"', tag)
        assert "aria-label=" in tag or (field_id and field_id.group(1) in labelled), \
            f"{name}: a form field with only a placeholder: {tag}"


@pytest.mark.parametrize("name", PAGES)
def test_new_tab_links_are_safe_and_say_so(name):
    for tag, body in re.findall(r'(<a\b[^>]*target="_blank"[^>]*>)(.*?)</a>', PAGES[name], re.S):
        assert 'rel="noopener noreferrer"' in tag, f"{name}: {tag}"
        assert "opens in a new tab" in body, f"{name}: new-tab link that doesn't say so: {tag}"


def test_status_colors_are_also_said_in_words():
    """WCAG 1.4.1: the dash's green/red dots can't be the only signal."""
    page = admin._DASH_PAGE
    assert "— up" in page and "— down" in page and "— needs a look" in page
    assert page.count('class="dot" aria-hidden="true"') >= 2


@pytest.mark.parametrize("name, markup", [
    ("catalog", '<h1><span aria-hidden="true">🎟️</span>'),
    ("catalog", '<span class="badge"><span aria-hidden="true">${EMOJI'),
    ("catalog", '<span aria-hidden="true">🗓️</span>'),
    ("catalog", '<span aria-hidden="true">🗑</span> Remove'),
    ("share", '<h1><span aria-hidden="true">📍</span>'),
    ("share", '<span class="badge"><span aria-hidden="true">${EMOJI'),
    ("share", '<span aria-hidden="true">👍</span>'),
    ("dash", '<span class="icon" aria-hidden="true">'),
])
def test_decorative_emoji_is_hidden_from_screen_readers(name, markup):
    """An emoji next to words that already say the same thing is read aloud as
    noise ("wastebasket Remove"), so it's hidden from screen readers."""
    assert markup in PAGES[name]
