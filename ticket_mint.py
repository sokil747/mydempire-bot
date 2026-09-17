"""Reserve exactly the goods required for the Imperial Ticket Mint.

Mirrors the game's own auto-selection (js/goods-ticket.js):
  - R1 3★: two from EVERY industry (10 goods), cheapest first
  - R2 2★+: 4 goods from different industries, fewest stars then cheapest
  - R3 2★+: 4 goods from different industries, fewest stars then cheapest
  - R4 1★+: 2 goods from different industries
  - R5 1★+: 1 good, any industry
Everything not selected is safe to redeem.
"""

from collections import defaultdict

TICKET_EMP_COST = 50
TICKET_R1_PER_INDUSTRY = 2
TICKET_R2_COUNT = 4
TICKET_R3_COUNT = 4
TICKET_R4_COUNT = 2
TICKET_R5_COUNT = 1

TICKET_INDUSTRIES = ("FOOD", "TEXTILE", "PHARMA", "CHEMICAL", "SUPERMARKET")

_STARS = {"STANDARD": 1, "FINE": 2, "SUPERIOR": 3}
_RANK = {"ESSENTIAL": 1, "STANDARD": 2, "VALUE": 3, "PREMIUM": 4, "LUXURY": 5}


def _norm(v) -> str:
    return str(v or "").strip().upper()


def _stars(quality) -> int:
    return _STARS.get(_norm(quality), 1)


def _rank(good) -> str:
    level = _norm(good) if isinstance(good, str) else _norm(good.get("product_level"))
    n = _RANK.get(level)
    if n:
        return f"R{n}"
    return level if level.startswith("R") else ""


def _sort_by_value(good):
    return (float(good.get("final_value") or 0), int(good.get("id") or 0))


def _sort_protect_quality(good):
    return (_stars(good.get("quality")),) + _sort_by_value(good)


def _across_industries(
    items: list, rank: str, min_stars: int, count: int, used: set
) -> list:
    """Pick `count` goods of a rank from different industries, preferring the
    lowest stars and lowest value (exactly like the game does)."""
    options = []
    for industry in TICKET_INDUSTRIES:
        candidates = sorted(
            (
                g for g in items
                if _rank(g.get("product_level")) == rank
                and _norm(g.get("industry")) == industry
                and _stars(g.get("quality")) >= min_stars
                and int(g.get("id")) not in used
            ),
            key=_sort_protect_quality,
        )
        if candidates:
            options.append(candidates[0])
    options.sort(key=_sort_protect_quality)
    selected = []
    for good in options:
        if len(selected) >= count:
            break
        selected.append(good)
        used.add(int(good.get("id")))
    return selected


def ticket_mint_reserved_ids(items: list) -> set[int]:
    """Return ids of the exact goods required for one Imperial Ticket."""
    used: set[int] = set()
    reserved: set[int] = set()

    # R1 3★ — two per industry, cheapest first
    for industry in TICKET_INDUSTRIES:
        candidates = sorted(
            (
                g for g in items
                if _rank(g.get("product_level")) == "R1"
                and _norm(g.get("industry")) == industry
                and _stars(g.get("quality")) == 3
                and int(g.get("id")) not in used
            ),
            key=_sort_by_value,
        )
        for good in candidates[:TICKET_R1_PER_INDUSTRY]:
            reserved.add(int(good.get("id")))
            used.add(int(good.get("id")))

    # R2 2★+ x4, R3 2★+ x4, R4 1★+ x2 — different industries
    for rank, min_stars, cnt in (
        ("R2", 2, TICKET_R2_COUNT),
        ("R3", 2, TICKET_R3_COUNT),
        ("R4", 1, TICKET_R4_COUNT),
    ):
        for good in _across_industries(items, rank, min_stars, cnt, used):
            reserved.add(int(good.get("id")))

    # R5 1★+ — one, any industry (protect quality: fewest stars first)
    r5 = sorted(
        (
            g for g in items
            if _rank(g.get("product_level")) == "R5"
            and _norm(g.get("industry")) in TICKET_INDUSTRIES
            and int(g.get("id")) not in used
        ),
        key=_sort_protect_quality,
    )
    if r5:
        reserved.add(int(r5[0].get("id")))
        used.add(int(r5[0].get("id")))

    return reserved