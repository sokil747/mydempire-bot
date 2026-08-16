"""Leaderboard position gathering for the daily report.

Each position (redemption, emperor, season) is fetched and formatted under its
own try/except so a single endpoint failure never breaks the whole report.
"""

import logging
from datetime import datetime, timedelta, timezone

from formatters import _int, _num

logger = logging.getLogger("mde_bot.leaderboard")

_SEASON_REWARD_EMP = {
    4: 500,
    5: 450,
    6: 400,
    7: 350,
    8: 300,
    9: 200,
    10: 100,
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _fmt_remaining(end: str | None) -> str:
    end_dt = _parse_iso(end)
    if end_dt is None:
        return "n/a"
    remaining = end_dt - datetime.now(timezone.utc)
    total = int(remaining.total_seconds())
    if total <= 0:
        return "ended"
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    return f"{hours}h {minutes}m"


def _season_reward_for_rank(rank: int) -> str | None:
    if rank == 1:
        return "5 HIVE + 1,100 EMP + Blueprint"
    if rank == 2:
        return "3 HIVE + 900 EMP + Blueprint"
    if rank == 3:
        return "2 HIVE + 700 EMP + Blueprint"
    emp = _SEASON_REWARD_EMP.get(rank)
    if emp is not None:
        return f"{emp:,} EMP"
    if rank <= 10:
        return "Rank 4-10: Blueprint lottery"
    return None


def _find_rank(rows: list, username: str, ep_key: str = "lifetime_ep") -> int | None:
    """Return 1-based rank of the user in a leaderboard list."""
    lower = username.lower()
    for i, row in enumerate(rows):
        if str(row.get("username") or "").lower() == lower:
            return i + 1
    return None


async def _redemption_report(api, username: str) -> str | None:
    try:
        pos = await api.goods_redemption_position(username)
    except Exception as exc:  # noqa: BLE001
        logger.exception("redemption position failed")
        return f"Redemption position unavailable: {exc}"
    if not pos.get("hasActiveCycle"):
        return "Redemption: no active cycle."
    player = pos.get("player") or {}
    cycle = pos.get("cycle") or {}
    rank = None
    try:
        lb = await api.goods_redemption_leaderboard()
        rank = _find_rank(lb.get("leaderboard") or [], username)
    except Exception as exc:  # noqa: BLE001
        logger.exception("redemption leaderboard failed")
        logger.error("redemption leaderboard error: %s", exc)
    lines = ["=== Redemption Leaderboard ==="]
    if rank is not None:
        lines.append(f"Position: #{rank}")
    lines.append(f"Your PV: {_num(player.get('product_value_in_cycle'))}")
    lines.append(f"Share: {_num(player.get('share_percent'))}%")
    lines.append(f"Estimated EMP: {_num(player.get('estimated_emp_now'))}")
    lines.append(f"Cycle ends in: {_fmt_remaining(cycle.get('ends_at'))}")
    return "\n".join(lines)


async def _emperor_report(api, username: str, reward_pool_hive: float | None) -> str | None:
    try:
        d = await api.emperor_leaderboard()
    except Exception as exc:  # noqa: BLE001
        logger.exception("emperor leaderboard failed")
        return f"Emperor leaderboard unavailable: {exc}"
    rows = sorted(
        (d.get("leaders") or []) or [],
        key=lambda r: float(r.get("lifetime_ep") or 0),
        reverse=True,
    )
    if not rows:
        return "Emperor leaderboard: no leaders yet."
    rank = _find_rank(rows, username)
    if rank is None:
        return "Emperor leaderboard: you are not ranked."
    me = rows[rank - 1]
    share = float(me.get("global_ep_share_percent") or 0)
    lines = [
        "=== Emperor Leaderboard ===",
        f"Position: #{rank}",
        f"Lifetime EP: {_num(me.get('lifetime_ep'))}",
        f"EP/day: {_num(me.get('current_ep_per_day'))}",
        f"Global share: {_num(share)}%",
    ]
    if reward_pool_hive is not None and share > 0:
        est = reward_pool_hive * share / 100.0
        lines.append(f"Estimated daily reward: {_num(est)} HIVE")
    return "\n".join(lines)


async def _season_report(api, username: str) -> str | None:
    try:
        active = await api.active_season()
    except Exception as exc:  # noqa: BLE001
        logger.exception("active season failed")
        return f"Season unavailable: {exc}"
    season = active.get("season")
    if not season:
        return "Season: no active season."
    lines = [
        "=== Season Leaderboard ===",
        f"Season: {season.get('name', 'n/a')} ({season.get('industry', 'n/a')})",
    ]
    ends = season.get("end_date")
    if ends:
        lines.append(f"Ends in: {_fmt_remaining(ends)}")
    try:
        lb = await api.season_leaderboard()
        rows = lb.get("leaderboard") or []
    except Exception as exc:  # noqa: BLE001
        logger.exception("season leaderboard failed")
        logger.error("season leaderboard error: %s", exc)
        rows = []
    if not rows:
        lines.append("No ranked players yet.")
        return "\n".join(lines)
    rank = _find_rank(rows, username, ep_key="ep")
    if rank is None:
        lines.append("You are not in the current season ranks.")
        return "\n".join(lines)
    me = rows[rank - 1]
    lines.append(f"Position: #{rank}")
    lines.append(f"Season EP/day: {_num(me.get('ep'))}")
    est = _season_reward_for_rank(rank)
    if est:
        lines.append(f"Estimated reward: {est}")
    return "\n".join(lines)


async def gather_leaderboard_report(api, username: str, reward_pool_hive: float | None) -> str:
    """Gather all leaderboard positions. Each section is independently safe."""
    parts = []
    for section in (
        _emperor_report(api, username, reward_pool_hive),
        _season_report(api, username),
        _redemption_report(api, username),
    ):
        result = await section
        if result:
            parts.append(result)
    return "\n\n".join(parts)