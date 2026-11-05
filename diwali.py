"""Diwali Festival automation (Nov 1-7, 2026).

Event rules (https://www.mydempire.com/diwali-festival.html):
- 100 EMP one-time registration; daily basket qualifies for that day's draw.
- Basket: any 3+ available goods totaling >=500 PV (any rarity/quality).
- Draws 15 HIVE + sealed NFT daily at 00:00 UTC; milestones: 3 days -> 50 EMP,
  7 days -> 100 EMP + 5 AP.

Automation:
- While the event is LIVE, goods auto-redemption is fully suspended (all
  available goods are kept for festival use).
- The basket is submitted right after each goods claim (+10s buffer), with an
  hourly self-healing retry in the scheduler loop as safety net.
- The basket uses the CHEAPEST qualifying goods (least PV per submission);
  ticket-mint reserved goods are used only as a last resort (when no
  500-PV basket is possible without them).
"""

import asyncio
import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger("mde_bot.diwali")

MIN_GOODS = getattr(config, "DIWALI_MIN_GOODS", 3)
MIN_PV = getattr(config, "DIWALI_MIN_PV", 500.0)


async def festival_live(api) -> bool:
    """True when the event status is LIVE (regardless of today's basket)."""
    try:
        state = await api.diwali_state()
    except Exception:  # noqa: BLE001
        return False
    event = state.get("event") or {}
    return str(event.get("status") or "").upper() == "LIVE"


async def redeem_suspended(api) -> bool:
    """True while the festival is LIVE: goods auto-redemption is suspended."""
    return await festival_live(api)


async def _diwali_active_and_open(api) -> bool:
    """True when the event is LIVE and today's basket is not yet submitted."""
    try:
        state = await api.diwali_state()
    except Exception:  # noqa: BLE001
        return False
    event = state.get("event") or {}
    if str(event.get("status") or "").upper() != "LIVE":
        return False
    try:
        p = await api.diwali_player(config.HIVE_USERNAME)
        player = p.get("player") or {}
    except Exception:  # noqa: BLE001
        return False
    return not player.get("today_basket")


def _cheapest_basket(items: list) -> tuple[list[int], float] | None:
    """Cheapest 3+ goods reaching 250 PV (selection core)."""
    candidates = sorted(
        items, key=lambda g: float(g.get("final_value") or 0)
    )
    picked: list = []
    total_pv = 0.0
    for g in candidates:
        if picked and total_pv >= MIN_PV:
            break
        picked.append(g)
        total_pv += float(g.get("final_value") or 0)
    if len(picked) < MIN_GOODS or total_pv < MIN_PV:
        return None
    return [int(g["id"]) for g in picked], total_pv


async def diwali_reserved_ids(api, items: list) -> set[int]:
    """Ids of ALL available goods while the festival is LIVE.

    During the event goods auto-redemption is fully suspended: nothing may
    be burned until the daily contribution is made and, with redemption off
    for the whole festival, inventory keeps accumulating for future days.
    Returns an empty set when the event is not live.
    """
    if not await festival_live(api):
        return set()
    return {int(i["id"]) for i in items if i.get("id")}


async def select_basket(api) -> tuple[list[int], float] | None:
    """Pick the cheapest 3+ goods reaching 500 PV from available inventory.

    Prefers goods not reserved for the Imperial Ticket Mint; only when no
    qualifying basket exists without them are ticket-mint goods included
    (event participation outranks ticket prep).
    """
    from ticket_mint import ticket_mint_reserved_ids

    inv = await api.goods_inventory(config.HIVE_USERNAME)
    items = [
        i for i in (inv.get("items") or [])
        if str(i.get("status") or "AVAILABLE").upper() == "AVAILABLE"
        and i.get("id")
    ]
    reserved = ticket_mint_reserved_ids(items) or set()
    basket = _cheapest_basket(
        [i for i in items if int(i["id"]) not in reserved]
    )
    if not basket:
        basket = _cheapest_basket(items)
    if not basket:
        return None
    return basket[0], basket[1]


async def run_daily_basket(api) -> str:
    """Submit today's Diwali basket once per UTC day when the event is LIVE.

    Returns a human-readable result; safe to call any time (skips when the
    event is upcoming/ended or today's basket is already submitted).
    """
    try:
        state = await api.diwali_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning("diwali state unavailable: %s", exc)
        return "Diwali: state unavailable."
    event = state.get("event") or {}
    status = str(event.get("status") or "").upper()
    if status != "LIVE":
        when = event.get("starts_at", "")[:10] if status == "UPCOMING" else "ended"
        return f"Diwali: {status.lower()} ({when})."

    p = await api.diwali_player(config.HIVE_USERNAME)
    player = p.get("player") or {}
    if player.get("today_basket"):
        days_done = int(player.get("days_completed") or 0)
        return (
            f"Diwali: today's basket already submitted "
            f"({days_done}/7 days completed)."
        )

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today_utc != str(player.get("today_event_date") or today):
        return "Diwali: player day not rolled over yet — retry later."

    basket = await select_basket(api)
    if not basket:
        return f"Diwali: not enough available PV (needs {MIN_GOODS}+ goods, {int(MIN_PV)}+ PV)."
    ids, total_pv = basket
    result = await api.diwali_submit_basket(
        config.HIVE_USERNAME, ids
    )
    submission = result.get("submission") or {}
    days_done = int(submission.get("days_completed") or (player.get("days_completed") or 0) + 1)
    milestones = [
        r.get("key") for r in (submission.get("milestones") or [])
    ]
    label = f"{len(ids)} goods, {round(total_pv)} PV"
    milestone_text = (
        " | milestones: " + ", ".join(milestones) if milestones else ""
    )
    try:
        import daily_log

        daily_log.log_action(
            "Diwali basket submitted",
            detail=f"{label}{milestone_text} ({days_done}/7 days)",
        )
    except ImportError:
        pass
    return (
        f"Diwali basket submitted: {label} — "
        f"{days_done}/7 days completed{milestone_text}"
    )


async def status_text(api) -> str:
    """Short Diwali status for reports/commands."""
    try:
        state = await api.diwali_state()
        event = state.get("event") or {}
    except Exception as exc:  # noqa: BLE001
        return f"Diwali: state unavailable ({exc})"
    status = str(event.get("status") or "?").upper()
    starts = str(event.get("starts_at") or "")[:10]
    ends = str(event.get("ends_at") or "")[:10]
    try:
        p = await api.diwali_player(config.HIVE_USERNAME)
        player = p.get("player") or {}
        days = int(player.get("days_completed") or 0)
        nfts = player.get("sealed_nfts") or []
    except Exception:  # noqa: BLE001
        days, nfts = 0, []
    return (
        f"Diwali Festival: {status.lower()} ({starts} – {ends}) | "
        f"baskets: {days}/7 | sealed NFTs: {len(nfts)}"
    )