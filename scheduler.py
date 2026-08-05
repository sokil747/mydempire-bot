"""Persistent planned-claim scheduler backed by a JSON state file.

The bot writes the next claim/action time for each task into ``state.json``.
This survives restarts, so a one-shot time stays recorded even if the process
is redeployed. On startup (and after each action) the scheduler reads planned
times, schedules the exact asyncio wake-up, runs the action, then updates the
stored time for the next cycle.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

_STATE_FILE = Path(__file__).resolve().parent / "state.json"
_LOCK = threading.Lock()
_TS_FMT = "%Y-%m-%dT%H:%M:%S%z"


def _now() -> datetime:
    return datetime.now().astimezone()


def _load() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(data: dict) -> None:
    _STATE_FILE.write_text(json.dumps(data, indent=2))


def get_planned(key: str) -> datetime | None:
    with _LOCK:
        raw = _load().get(key)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, _TS_FMT)
    except (ValueError, TypeError):
        return None


def set_planned(key: str, when: datetime) -> None:
    with _LOCK:
        data = _load()
        data[key] = when.strftime(_TS_FMT)
        _save(data)


def clear_planned(key: str) -> None:
    with _LOCK:
        data = _load()
        if not data.pop(key, None):
            return
        _save(data)


def planned_seconds_until(key: str) -> float | None:
    when = get_planned(key)
    if when is None:
        return None
    return (when - _now()).total_seconds()