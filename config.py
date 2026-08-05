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


TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
HIVE_USERNAME = _required("HIVE_USERNAME").replace("@", "").strip().lower()
HIVE_POSTING_KEY = _required("HIVE_POSTING_KEY")
MDE_API_BASE = os.getenv("MDE_API_BASE", "https://mydempire-backend-1.onrender.com").rstrip("/")

# Daily time (HH:MM, VPS local time) for the scheduled daily tasks.
GOODS_CLAIM_CRON_TIME = "02:00"

# Telegram chat id to receive daily task notifications.
# Leave as None to only log notifications (no Telegram message).
GOODS_CLAIM_NOTIFY_CHAT_ID = None

# ---- empire operations automation ----
# Operation type to run each day (LOCAL_SUPPLY / REGIONAL_TRADE / IMPERIAL_EXPANSION).
OPS_TYPE = "LOCAL_SUPPLY"
# EMP budget to commit per operation.
OPS_BUDGET = 25
# How many operations to start per day.
OPS_PER_DAY = 3

# ---- factory fulfillment automation ----
# Fulfillment type to start after claiming (STANDARD_BATCH / BULK_SHIPMENT / GRAND_CONSIGNMENT).
FULFILLMENT_TYPE = "GRAND_CONSIGNMENT"
# Buffer seconds added after estimated completion before claiming.
FULFILLMENT_CLAIM_BUFFER_SECONDS = 2
