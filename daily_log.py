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
    """Compile the 23:58 report: summary, EMP aggregates, assets, leaderboards."""
    lines = [f"===== Daily Report {datetime.now().strftime('%Y-%m-%d')} =====", ""]

    # 1) EMP ledger aggregated per source (manual + bot, from the game ledger)
    lines.append("=== EMP Summary Today ===")
    try:
        d = await api.dashboard(username)
        bal_now = float(d.get("empBalance") or 0)
        lines.append(f"Current balance: {round(bal_now, 2)} EMP")
    except Exception:  # noqa: BLE001
        bal_now = None
    try:
        h = await api.emp_history(username)
        items = h.get("history") or []
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        todays = [i for i in items if str(i.get("created_at", "")).startswith(today)]
        if todays:
            per_source: dict[str, dict] = {}
            grand = 0.0
            for it in todays:
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
            lines.append("Per-source totals (in / out / net):")
            for src, b in sorted(per_source.items()):
                net = b["in"] - b["out"]
                lines.append(
                    f"  {src}: +{round(b['in'], 2)} / -{round(b['out'], 2)} "
                    f"(net {round(net, 2)}) x{b['n']}"
                )
            lines.append(f"Net EMP today: {round(grand, 2)}")
            if bal_now is not None:
                lines.append(f"Net EMP today (by sources): {round(grand, 2)} | balance now: {round(bal_now, 2)}")
        else:
            lines.append("No EMP transactions recorded today.")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"EMP history unavailable: {exc}")
    lines.append("")

    # 2) Asset snapshot (current numbers; change vs start-of-day when stored)
    lines.append("=== Assets ===")
    try:
        d = await api.dashboard(username)
        snap = load_asset_snapshot()
        now_map = {
            "EMP_balance": round(float(d.get("empBalance") or 0), 2),
            "Factories_active_total": f"{d.get('activeFactories')}/{d.get('totalFactories')}",
            "EP_day": float(d.get("currentEpDay") or 0),
            "Lifetime_EP": float(d.get("lifetimeEP") or 0),
            "SMP": d.get("smpBalance"),
            "Relics": d.get("relicCount"),
        }
        # land/blueprint breakdown
        try:
            o = await api.empire_overview(username)
            lands = o.get("lands") or []
            from collections import Counter
            land_tiers = Counter()
            factory_tiers = Counter()
            for land in lands:
                land_tiers[str(land.get("land_tier") or "L1")] += 1
                for f in land.get("factories") or []:
                    factory_tiers[str(f.get("blueprint_tier") or "?")] += 1
            now_map["Lands"] = ", ".join(f"{k}:{v}" for k, v in sorted(land_tiers.items()))
            now_map["Factories_by_tier"] = ", ".join(
                f"{k}:{v}" for k, v in sorted(factory_tiers.items())
            )
        except Exception:  # noqa: BLE001
            pass
        if snap:
            lines.append(f"Snapshot at: {snap.get('savedAt', 'n/a')}")
            for key, cur in now_map.items():
                old = snap.get(key)
                lines.append(f"  {key}: was {old} -> now {cur}")
        else:
            for key, cur in now_map.items():
                lines.append(f"  {key}: {cur}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Asset snapshot unavailable: {exc}")
    lines.append("")

    # 3) Leaderboard positions
    lines.append("=== Leaderboard Positions ===")
    if leaderboard_text:
        lines.append(leaderboard_text)
    else:
        lines.append("(unavailable)")

    return "\n".join(lines)


def archive_day() -> None:
    """Clear the snapshot after the report is sent (file stays as archive)."""
    clear_asset_snapshot()
