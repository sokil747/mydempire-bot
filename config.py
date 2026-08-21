import os

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Fill it in the .env file."
        )
    return value


def _opt_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _opt_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
HIVE_USERNAME = _required("HIVE_USERNAME").replace("@", "").strip().lower()
HIVE_POSTING_KEY = _required("HIVE_POSTING_KEY")
MDE_API_BASE = os.getenv("MDE_API_BASE", "https://mydempire-backend-1.onrender.com").rstrip("/")

# ---- daily cron ----
# Daily time (HH:MM, VPS local time) for the scheduled daily tasks.
GOODS_CLAIM_CRON_TIME = os.getenv("GOODS_CLAIM_CRON_TIME", "02:00")

# Telegram chat id to receive the daily report and background notifications.
# Set to a numeric chat id in .env (e.g. GOODS_CLAIM_NOTIFY_CHAT_ID=123456789).
GOODS_CLAIM_NOTIFY_CHAT_ID = _opt_int("GOODS_CLAIM_NOTIFY_CHAT_ID")

# Telegram chat id for the full daily report specifically.
DAILY_REPORT_CHAT_ID = _opt_int("DAILY_REPORT_CHAT_ID")

# ---- empire operations automation ----
# Operation type to run each day (LOCAL_SUPPLY / REGIONAL_TRADE / IMPERIAL_EXPANSION).
OPS_TYPE = os.getenv("OPS_TYPE", "LOCAL_SUPPLY")
# EMP budget to commit per operation.
OPS_BUDGET = int(os.getenv("OPS_BUDGET", "25"))
# How many operations to start per day.
OPS_PER_DAY = int(os.getenv("OPS_PER_DAY", "3"))

# ---- factory fulfillment automation ----
# Fulfillment type to start after claiming (STANDARD_BATCH / BULK_SHIPMENT / GRAND_CONSIGNMENT).
FULFILLMENT_TYPE = os.getenv("FULFILLMENT_TYPE", "GRAND_CONSIGNMENT")
# Buffer seconds added after estimated completion before claiming.
FULFILLMENT_CLAIM_BUFFER_SECONDS = int(os.getenv("FULFILLMENT_CLAIM_BUFFER_SECONDS", "2"))

# ---- goods auto-redemption ----
# When enabled (on by default), after goods are claimed the bot bulk-redeems
# all AVAILABLE goods on the inventory tab via the redemption burn endpoint.
AUTO_REDEMPTION = _opt_bool("AUTO_REDEMPTION", True)

# ---- daily statistics to Google Sheets ----
# When enabled (on by default), the 02:00 daily run gathers game statistics
# and appends a row (one per day, dd/mm/yyyy) to a Google Spreadsheet.
STATS_ENABLED = _opt_bool("STATS_ENABLED", True)

# ID of the spreadsheet to write daily statistics into (from its URL).
# STATS_SPREADSHEET_ID=""
STATS_SPREADSHEET_ID = os.getenv("STATS_SPREADSHEET_ID", "").strip()

# Local path to the Google service-account JSON key with edit access to the
# spreadsheet. STATS_SPREADSHEET_ID:
# STATS_SERVICE_ACCOUNT_FILE=""
STATS_SERVICE_ACCOUNT_FILE = os.getenv("STATS_SERVICE_ACCOUNT_FILE", "").strip()

# ---- crate claiming ----
# Maximum number of Imperial Supply Crates that can be opened per day.
# Default: 4 (set to 1 for original "1 free crate per day" behavior).
CRATE_MAX_CLAIMS_PER_DAY = int(os.getenv("CRATE_MAX_CLAIMS_PER_DAY", "4"))