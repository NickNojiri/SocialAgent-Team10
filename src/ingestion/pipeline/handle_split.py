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
    """.split()
)

# Longest first so greedy match prefers whole words.
_MAX_WORD = max(len(w) for w in _WORDS)


def _titlecase(parts: list[str]) -> str:
    return " ".join(p[:1].upper() + p[1:] for p in parts if p)


def split_handle(handle: str) -> str:
    """'waterfallchicken' -> 'Waterfall Chicken'; unknown runs stay whole."""
    core = handle.strip().lstrip("@").split("/")[0].lower()
    if not core:
        return ""
    # Explicit separators first — trivially correct.
    if any(c in core for c in "._-"):
        import re

        return _titlecase(re.split(r"[._\-]+", core))
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
        else:  # no word matched at this position → give up, keep the whole handle
            return core[:1].upper() + core[1:]
    # Only trust a multi-word split (a single "word" is just the whole handle).
    return _titlecase(parts) if len(parts) >= 2 else core[:1].upper() + core[1:]
