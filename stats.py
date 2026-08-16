"""Daily statistics gathering and write to a Google Spreadsheet.

All statistics are collected in one place (``gather_stats``) and then written
as one row per day (date in dd/mm/yyyy format) to the configured spreadsheet.

Disabled entirely when ``config.STATS_ENABLED`` is False.
"""

from datetime import datetime
from pathlib import Path

import gspread

import config

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


def _sheet_client() -> gspread.Client:
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        config.STATS_SERVICE_ACCOUNT_FILE, scopes=_SCOPES
    )
    return gspread.authorize(creds)


def _num(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0


async def gather_stats(api) -> dict:
    """Collect every statistic tracked for the spreadsheet.

    Single owner of all statistic tasks: add new stats here and they will be
    written with the daily row automatically.
    """
    health = await api.global_health()
    gstats = await api.global_stats()

    ep = health.get("ep") or {}
    treasury = health.get("treasury") or {}

    return {
        "date": datetime.now().strftime("%d/%m/%Y"),
        "globalProductionEpDay": _num(ep.get("liveGlobalEP")),
        "rewardCycleBaseEp": _num(ep.get("rewardCycleEP")),
        "treasuryHealthHive": _num(treasury.get("balance")),
        "rewardPoolHive": _num(treasury.get("rewardPool")),
        "packsSold": int(gstats.get("totalPacksSold") or 0),
        "activeFactories": int(gstats.get("activeFactories") or 0),
        "buildingFactories": int(gstats.get("buildingFactories") or 0),
        "inactiveFactories": int(gstats.get("inactiveFactories") or 0),
    }


def write_daily_row(stats: dict) -> None:
    """Append one row for today's stats to the configured spreadsheet.

    Creates the header row on first write. Runs in a worker thread from the
    caller so it never blocks the event loop.
    """
    import logging

    logger = logging.getLogger("mde_bot.stats")

    columns = [
        "Date",
        "Global Production (EP/day)",
        "Today's reward cycle base (EP)",
        "Treasury Health (HIVE)",
        "Today's reward pool (HIVE)",
        "Sold packs",
        "Active factories",
        "Building factories",
        "Inactive factories",
    ]
    row = [
        stats["date"],
        stats["globalProductionEpDay"],
        stats["rewardCycleBaseEp"],
        stats["treasuryHealthHive"],
        stats["rewardPoolHive"],
        stats["packsSold"],
        stats["activeFactories"],
        stats["buildingFactories"],
        stats["inactiveFactories"],
    ]

    client = _sheet_client()
    sheet = client.open_by_key(config.STATS_SPREADSHEET_ID)
    worksheet = sheet.sheet1

    first = worksheet.row_values(1)
    if not first:
        worksheet.append_row(columns)
    elif first != columns:
        for i, col in enumerate(columns, start=1):
            worksheet.update_cell(1, i, col)

    worksheet.append_row(row)
    logger.info("daily stats row written: %s", row)


async def run_stats_task(api) -> dict:
    """Gather all statistics and write today's row to the spreadsheet.

    Returns the stats dict. Raises if disabled config is missing or the write
    fails, so the caller can log/notify.
    """
    import asyncio

    if not config.STATS_ENABLED:
        return {}
    if not config.STATS_SPREADSHEET_ID:
        raise RuntimeError(
            "STATS_ENABLED is true but STATS_SPREADSHEET_ID is not set."
        )
    sa_file = Path(config.STATS_SERVICE_ACCOUNT_FILE)
    if not sa_file.exists():
        raise FileNotFoundError(
            f"Service account file not found: {sa_file} "
            "(set STATS_SERVICE_ACCOUNT_FILE and share the spreadsheet with it)."
        )
    stats = await gather_stats(api)
    await asyncio.to_thread(write_daily_row, stats)
    return stats