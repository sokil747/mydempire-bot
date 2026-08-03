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
