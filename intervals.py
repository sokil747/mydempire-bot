"""All timing and interval parameters in one place.

Tune these values without touching the bot code.
"""

# ---- API client ----
# Request timeout for each HTTP call.
API_TIMEOUT_SECONDS = 20

# ---- maintenance payments ----
# Random delay range (seconds) between each maintenance payment request.
# A random value between MIN and MAX is used to avoid 429 rate limits.
PAY_MAINT_DELAY_MIN = 2.0
PAY_MAINT_DELAY_MAX = 5.0

# Max retries when the backend returns HTTP 429 (rate limited).
PAY_MAINT_RETRIES = 3

# Backoff between retry attempts, multiplied each retry.
PAY_MAINT_RETRY_BASE_SECONDS = 5.0

# ---- Telegram polling ----
# Timeout for long-polling updates from Telegram.
TG_POLLING_TIMEOUT_SECONDS = 30

# ---- scheduled goods claim ----
# Buffer seconds added after the cooldown expires before claiming.
GOODS_CLAIM_BUFFER_SECONDS = 1

# Max attempts to check claim readiness at cron time before giving up.
GOODS_CLAIM_MAX_CHECK_ATTEMPTS = 3

# Delay between readiness re-checks (seconds).
GOODS_CLAIM_RECHECK_DELAY_SECONDS = 10.0

# Timeout for the scheduled claim action (seconds).
GOODS_CLAIM_ACTION_TIMEOUT_SECONDS = 30

# ---- imperial supply crate ----
# Cooldown between crate openings (seconds).
CRATE_COOLDOWN_SECONDS = 3 * 60 * 60

# Maximum crates that can be opened per day.
CRATE_MAX_PER_DAY = 4
