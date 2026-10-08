"""Segment a run-together Instagram handle into words — in-house, no dependency.

'@waterfallchicken' -> 'Waterfall Chicken'. Greedy longest-match left-to-right
over a small bundled vocabulary of common English + food/place/venue words. If it
can't segment cleanly it returns the input Title-cased (the old behaviour), so
this never makes a handle worse.

The vocabulary is deliberately small and hand-picked — it only has to cover the
words that show up in venue handles, not all of English. Grow it from /dash
corrections (docs/EXTRACTION_ACCURACY.md, the label loop).
"""

from __future__ import annotations

_WORDS: set[str] = set(
    """
    a an the and of to in on at by for with from up out off over
    breakfast brunch lunch dinner supper snack meal food eats eatery kitchen
    restaurant cafe coffee espresso tea boba matcha juice bar pub tavern
    bakery pastry bread donut doughnut cake pie cookie churro crepe waffle
    dessert sweet sweets candy chocolate cream gelato custard pudding
    taco tacos taqueria burrito quesadilla nacho nachos torta tamale asada
    birria carnitas pastor pollo pescado mariscos ceviche
    burger burgers fries shake diner grill smokehouse barbecue bbq brisket
    rib ribs wing wings chicken fried katsu tender nugget
    pizza pizzeria pasta noodle noodles ramen udon soba pho bun banh mi
    sushi sashimi omakase nigiri roll poke bowl donburi teriyaki tempura
    dumpling dim sum bao dan tan hotpot shabu
    naan curry tikka masala biryani kebab shawarma falafel hummus gyro
    steak seafood oyster oysters lobster crab shrimp fish clam
    wine beer brew brewery brewing cider mead ale lager stout hop hops
    cocktail cocktails spirits whiskey gin rum vodka tequila mezcal
    market popup pop shop store bodega grocer grocery deli market provisions
    farm garden orchard ranch mill house home haus hall room lounge club
    corner spot place joint stand cart truck kitchen table plate fork spoon
    road street avenue lane drive way boulevard plaza court alley row yard
    sugar salt spice pepper honey butter oil garlic ginger lemon lime mint
    smoked crispy juicy spicy sweet savory fresh hot cold iced frozen
    golden silver copper iron stone brick oak pine maple cedar willow
    river lake creek falls waterfall bay beach coast harbor port island
    hill hills valley canyon mesa ridge peak summit grove park meadow
    north south east west central downtown uptown midtown old new little big
    red blue green black white gray grey brown pink purple orange yellow
    sun moon star sky cloud rain storm fire flame smoke ash ember
    fox bear wolf lion tiger bird crow raven owl hawk eagle deer elk
    cat dog pig cow hen rooster goat sheep horse rabbit
    king queen prince duke lady lord saint royal crown court
    happy lucky merry jolly cozy rustic modern classic vintage craft
    daily nightly weekly morning noon night midnight dawn dusk
    brother brothers sister sisters family friend friends folks people
    mister missus madam senor senora chef cook baker butcher grocer
    company co bros son sons daughter and social supply provisions goods
    cheesecake cheese factory bagel bamboo bean malatang chop label
    grounded sapo dolce buena onda izakaya tokyo willow whisk
    always day days hello united snacks box boxes max bad good best china seoul
    shack shake stew pura vida casa mi su tu el la los las de del amor bonita
    piccolo bella bello dolci pane pan panaderia fleur fleurs et sel le petit
    maison chez dimsum dumplings tea teas cha milk bun buns roll rolls bowl bowls
    garden gardens corner table tables plates kitchen kitchens cantina bistro
    taste tasty yummy yum love lovely sweet sweets treat treats bite bites
    world city town village street side station central express fresh
    golden lucky happy fortune dragon phoenix tiger panda lotus jade pearl
    soft serve serving creamy sugar spice nice cafe coffee matcha
    """.split()
)

import re  # noqa: E402

# Longest first so greedy match prefers whole words.
_MAX_WORD = max(len(w) for w in _WORDS)

# Trailing city / market abbreviations tacked onto a handle: "@mizuri.la",
# "@oishibasd", "@chinamaxsandiego". Stripped only when a real name remains.
_GEO_SUFFIXES = (
    "sandiego", "losangeles", "sanfrancisco", "orangecounty", "sacramento",
    "sac", "sd", "oc", "la", "nyc", "chi", "sf", "dtla", "sgv", "lbc", "usa", "us",
    "socal", "bayarea", "chicago", "seattle", "torrance", "cerritos", "irvine",
    "anaheim", "pasadena", "fullerton", "carlsbad", "escondido", "tustin",
    "hayward", "alhambra", "vegas", "miami", "boston", "dallas", "toronto",
    "vancouver", "amsterdam", "melbourne", "perth", "seoul", "tokyo", "co", "com",
    "houston", "charlotte", "austin", "denver", "atlanta", "phoenix", "portland",
    "nashville", "philly", "sydney", "london", "brooklyn", "manhattan", "honolulu",
)
# Handle tails that are venue category words — peel them to expose the head.
_TAIL_WORDS = (
    "restaurants", "restaurant", "coffeehouse", "coffee", "cafe", "kitchen",
    "bakery", "bar", "house", "grill", "pizzeria", "teaco", "tea", "creamery",
    "deli", "eatery", "tavern", "market", "club", "lounge", "company",
)


def _titlecase(parts: list[str]) -> str:
    return " ".join(p[:1].upper() + p[1:] for p in parts if p)


def _strip_geo_suffix(core: str) -> str:
    for _ in range(2):
        for suf in _GEO_SUFFIXES:
            if core.endswith(suf) and len(core) - len(suf) >= 4:
                core = core[: -len(suf)]
                break
        else:
            break
    return core


def _peel_tail(core: str) -> list[str]:
    """'bjsrestaurants' -> ['bjs','restaurants']; 'sapocoffeehouse' -> ['sapo','coffee','house']."""
    tail: list[str] = []
    for _ in range(3):
        for w in _TAIL_WORDS:
            if core.endswith(w) and len(core) - len(w) >= 3:
                tail.insert(0, w)
                core = core[: -len(w)]
                break
        else:
            break
    return ([core] if core else []) + tail


def split_handle(handle: str) -> str:
    """'waterfallchicken' -> 'Waterfall Chicken'; unknown runs stay whole."""
    core = handle.strip().lstrip("@").split("/")[0].lower()
    if not core:
        return ""
    if any(c in core for c in "._-"):
        parts = re.split(r"[._\-]+", core)
        if len(parts) > 1 and len(parts[-1]) <= 3 and _strip_geo_suffix("".join(parts)) != "".join(parts):
            parts = parts[:-1] or parts     # drop a trailing "la"/"sd"/"oc"
        return _titlecase(parts)

    stripped = _strip_geo_suffix(core)
    if stripped != core and len(stripped) >= 4:
        core = stripped
    if core in _WORDS or len(core) <= 4:
        return core[:1].upper() + core[1:]

    parts: list[str] = []
    i, n = 0, len(core)
    while i < n:
        for length in range(min(_MAX_WORD, n - i), 2, -1):
            if core[i : i + length] in _WORDS:
                parts.append(core[i : i + length])
                i += length
                break
        else:
            best = _segment(core)              # greedy failed — allow one name part
            if best:
                return _titlecase(best)
            peeled = _peel_tail(core)          # ...or peel a tail word
            return _titlecase(peeled) if len(peeled) >= 2 else core[:1].upper() + core[1:]
    return _titlecase(parts) if len(parts) >= 2 else core[:1].upper() + core[1:]


def _segment(core: str) -> list[str] | None:
    """Best split of `core` into known words plus at most ONE unknown run — the part
    that is a proper name ('tuttobelle|gelato', 'chef|fei', 'sushi|payce'). None
    when no such split covers most of the handle with real words."""
    n = len(core)
    # best[i] = (unknown_runs, unknown_chars, pieces, parts) for core[:i]
    best: list[tuple[int, int, int, list[str]] | None] = [None] * (n + 1)
    best[0] = (0, 0, 0, [])
    for i in range(n):
        if best[i] is None:
            continue
        u, uc, k, parts = best[i]
        for j in range(i + 1, n + 1):
            piece = core[i:j]
            known = piece in _WORDS and len(piece) >= 3     # 'tu'/'le' would chop names apart
            cand = (u, uc, k + 1, parts + [piece]) if known else (
                (u + 1, uc + len(piece), k + 1, parts + [piece])
                if len(piece) >= 2 and not (parts and parts[-1] not in _WORDS) else None)
            if cand is None or cand[0] > 1:
                continue
            if best[j] is None or cand[:3] < best[j][:3]:
                best[j] = cand
    res = best[n]
    if not res or len(res[3]) < 2:
        return None
    unknown_runs, unknown_chars, _, parts = res
    if unknown_runs > 1 or unknown_chars * 3 > n * 2:   # real words cover at least a third
        return None
    if len(parts[-1]) == 3 and parts[-2] not in _WORDS:  # 'eggb|red' — a cut, not a word
        return None
    return parts
