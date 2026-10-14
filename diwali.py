"""Diwali Festival automation (Nov 1-7, 2026).

Daily UTC basket: >=3 goods totaling >=250 PV qualify for the midnight draw
(15 HIVE to one participant + one sealed NFT to another) and milestones
(3 days -> 50 EMP, 7 days -> 100 EMP + 5 AP).

The basket uses the CHEAPEST qualifying goods so the least PV leaves the
inventory; ticket-mint goods are never selected.
"""

import logging
from datetime import datetime, timezone

import config

logger = logging.getLogger("mde_bot.diwali")

MIN_GOODS = 3
MIN_PV = 250


async def select_basket(api) -> tuple[list[int], float] | None:
    """Pick the cheapest 3+ goods reaching 250 PV from available inventory.

    Excludes ticket-mint reserved goods. Returns (ids, total_pv) or None.
    """
    try:
        from ticket_mint import ticket_mint_reserved_ids
    except ImportError:
        def ticket_mint_reserved_ids(items):  # noqa: F841
            return set()
    from mde_api import MydEmpireClient  # noqa: F401 (typing only)

    inv = await api.goods_inventory(config.HIVE_USERNAME)
    items = [
        i for i in (inv.get("items") or [])
        if str(i.get("status") or "AVAILABLE").upper() == "AVAILABLE"
        and i.get("id")
    ]
    reserved = ticket_mint_reserved_ids(items)
    candidates = sorted(
        (i for i in items if int(i["id"]) not in reserved),
        key=lambda g: float(g.get("final_value") or 0),
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
        return "Diwali: not enough available PV (needs 3+ goods, 250+ PV)."
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