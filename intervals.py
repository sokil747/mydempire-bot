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
GOODS_CLAIM_BUFFER_SECONDS = 3

# Max attempts to check claim readiness at cron time before giving up.
GOODS_CLAIM_MAX_CHECK_ATTEMPTS = 3

# Delay between readiness re-checks (seconds).
GOODS_CLAIM_RECHECK_DELAY_SECONDS = 10.0

# Timeout for the scheduled claim action (seconds).
GOODS_CLAIM_ACTION_TIMEOUT_SECONDS = 30

# How often the scheduler re-reads state.json to recover planned tasks.
# This is a cheap local file read (no API polling) and acts as a restart
# safety net; the precise action fires at its persisted planned time via an
# asyncio task, not from repeated API checks.
GOODS_SCHEDULER_REFRESH_SECONDS = 300

# Maximum how far ahead the scheduler may run a task (safety cap).
GOODS_SCHEDULE_MAX_AHEAD_SECONDS = 30 * 24 * 60 * 60

# ---- activity wheel ----
# How often to check for available wheel spins outside the daily 02:00 run.
# A spin earned mid-day sits unused until the next daily run otherwise.
WHEEL_RECHECK_INTERVAL_SECONDS = 30 * 60

# How often to check while the wheel has no spins available (slower).
WHEEL_RECHECK_EMPTY_INTERVAL_SECONDS = 6 * 60 * 60

# ---- imperial supply crate ----
# Cooldown between crate openings (seconds).
CRATE_COOLDOWN_SECONDS = 3 * 60 * 60

# Maximum crates that can be opened per day.
CRATE_MAX_PER_DAY = 4

# ---- factory fulfillment restart ----
# Delay (seconds) between claiming a completed fulfillment and starting a
# new one.
FULFILLMENT_RESTART_DELAY_SECONDS = 10

# ---- empire operations automation ----
# How often to poll for operation readiness while waiting (seconds).
OPS_POLL_INTERVAL_SECONDS = 60

# Max attempts to collect an operation before giving up.
OPS_COLLECT_ATTEMPTS = 5

# Backoff between collect retries (seconds).
OPS_COLLECT_RETRY_SECONDS = 30
