"""Daily activity log with 23:58 summary report.

Bot actions are logged in real time (time, name, EMP result) into
``logs/daily_report_YYYY-MM-DD.txt``. At 23:58 a full report is compiled:
  - every bot action from the log,
  - every EMP transaction of the day from the game emp-history ledger
    (covers manual in-game actions too),
  - asset changes vs the snapshot taken at the start of the day,
  - current leaderboard positions.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import scheduler

import config

LOG_DIR = Path(__file__).resolve().parent / "logs"

_SNAPSHOT_KEY = "daily_asset_snapshot"


def _today_file() -> Path:
    return LOG_DIR / f"daily_report_{datetime.now().strftime('%Y-%m-%d')}.txt"


def log_action(name: str, emp: float | None = None, detail: str = "") -> None:
    """Append one bot action to today's daily log file (and archive file)."""
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        parts = [f"[{ts}] {name}"]
        if emp is not None:
            sign = "+" if emp >= 0 else ""
            parts.append(f"EMP: {sign}{round(float(emp), 2)}")
        if detail:
            parts.append(detail)
        line = " | ".join(parts)
        _today_file().parent.mkdir(parents=True, exist_ok=True)
        with _today_file().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def read_today_log() -> str:
    try:
        if _today_file().exists():
            return _today_file().read_text(encoding="utf-8").rstrip()
    except OSError:
        pass
    return "(no actions logged today)"


def save_asset_snapshot(d: dict) -> None:
    """Store the current asset snapshot in state.json (start-of-day)."""
    snap = {
        "EMP_balance": d.get("empBalance"),
        "Factories_active_total": f"{d.get('activeFactories')}/{d.get('totalFactories')}",
        "EP_day": d.get("currentEpDay"),
        "Lifetime_EP": d.get("lifetimeEP"),
        "SMP": d.get("smpBalance"),
        "Relics": d.get("relicCount"),
        "savedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    try:
        data = json.loads(scheduler._STATE_FILE.read_text())
        data[_SNAPSHOT_KEY] = snap
        scheduler._STATE_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def load_asset_snapshot() -> dict | None:
    try:
        data = json.loads(scheduler._STATE_FILE.read_text())
        snap = data.get(_SNAPSHOT_KEY)
        return snap if isinstance(snap, dict) else None
    except Exception:
        return None


def clear_asset_snapshot() -> None:
    try:
        data = json.loads(scheduler._STATE_FILE.read_text())
        if data.pop(_SNAPSHOT_KEY, None) is not None:
            scheduler._STATE_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


_EMP_RE = re.compile(r"(-?[\d.,]+)\s*EMP")


def _sum_emp_from_log(text: str) -> float:
    total = 0.0
    for m in _EMP_RE.finditer(text):
        try:
            total += float(m.group(1).replace(",", ""))
        except ValueError:
            continue
    return total


async def build_daily_report(api, username: str, leaderboard_text: str | None = None) -> str:
    """Compile the 23:58 report: action log, EMP ledger, assets, leaderboard."""
    lines = [f"===== Daily Report {datetime.now().strftime('%Y-%m-%d')} =====", ""]

    # 1) Bot action log
    lines.append("=== Actions Today (bot) ===")
    log_text = read_today_log()
    lines.append(log_text)
    lines.append("")

    # 2) EMP ledger for today (manual + bot, from the game ledger)
    lines.append("=== EMP Ledger Today (all actions incl. manual) ===")
    bot_total = _sum_emp_from_log(log_text)
    try:
        h = await api.emp_history(username)
        items = h.get("history") or []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        todays = [i for i in items if str(i.get("created_at", "")).startswith(today)]
        if todays:
            per_source: dict[str, dict] = {}
            grand = 0.0
            for it in reversed(todays):  # chronological
                amt = float(it.get("amount") or 0)
                direction = str(it.get("direction") or "").upper()
                delta = amt if direction == "IN" else -amt
                grand += delta
                src = it.get("source") or "OTHER"
                bucket = per_source.setdefault(src, {"in": 0.0, "out": 0.0, "n": 0})
                bucket["n"] += 1
                if direction == "IN":
                    bucket["in"] += amt
                else:
                    bucket["out"] += amt
                lines.append(
                    f"  {it.get('created_at', '')[11:16]} {src}: "
                    f"{direction} {round(amt, 2)} EMP — {it.get('note', '')}"
                )
            lines.append("")
            lines.append("Per-source totals:")
            for src, b in sorted(per_source.items()):
                net = b["in"] - b["out"]
                lines.append(
                    f"  {src}: +{round(b['in'], 2)} / -{round(b['out'], 2)} "
                    f"(net {round(net, 2)}) x{b['n']}"
                )
            lines.append(f"Ledger net EMP today: {round(grand, 2)}")
        else:
            lines.append("No EMP transactions recorded today.")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"EMP history unavailable: {exc}")
    lines.append("")

    # 3) Asset changes vs start-of-day snapshot
    lines.append("=== Asset Changes ===")
    try:
        d = await api.dashboard(username)
        snap = load_asset_snapshot()
        now_map = {
            "EMP balance": float(d.get("empBalance") or 0),
            "Factories (active/total)": f"{d.get('activeFactories')}/{d.get('totalFactories')}",
            "EP/day": float(d.get("currentEpDay") or 0),
            "Lifetime EP": float(d.get("lifetimeEP") or 0),
            "SMP": float(d.get("smpBalance") or 0),
            "Relics": d.get("relicCount"),
        }
        if snap:
            lines.append(f"Snapshot at: {snap.get('savedAt', 'n/a')}")
            for key, cur in now_map.items():
                old = snap.get(key.replace(" ", "_"), None)
                lines.append(f"  {key}: was {old} -> now {cur}")
        else:
            for key, cur in now_map.items():
                lines.append(f"  {key}: {cur} (no start-of-day snapshot)")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Asset snapshot unavailable: {exc}")
    lines.append("")

    # 4) Leaderboard positions
    lines.append("=== Leaderboard Positions ===")
    if leaderboard_text:
        lines.append(leaderboard_text)
    else:
        lines.append("(unavailable)")

    return "\n".join(lines)


def archive_day() -> None:
    """Clear the snapshot after the report is sent (file stays as archive)."""
    clear_asset_snapshot()
