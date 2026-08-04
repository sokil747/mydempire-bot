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

# Daily time (HH:MM, VPS local time) for the scheduled goods claim cron.
GOODS_CLAIM_CRON_TIME = os.getenv("GOODS_CLAIM_CRON_TIME", "02:00").strip()

# Telegram chat id to receive auto-claim notifications (optional).
GOODS_CLAIM_NOTIFY_CHAT_ID = os.getenv("GOODS_CLAIM_NOTIFY_CHAT_ID", "").strip()
try:
    GOODS_CLAIM_NOTIFY_CHAT_ID = int(GOODS_CLAIM_NOTIFY_CHAT_ID)
except ValueError:
    GOODS_CLAIM_NOTIFY_CHAT_ID = None
