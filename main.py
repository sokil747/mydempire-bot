import asyncio
import html
import logging
import random
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import config
import intervals
import scheduler
from formatters import (
    _int,
    _num,
    format_crate_open,
    format_crate_plan,
    format_fulfillment_claim,
    format_fulfillment_start,
    format_fulfillment_status,
    format_global_stats,
    format_goods_claim,
    format_goods_claim_plan,
    format_goods_preview,
    format_lands,
    format_maintenance,
    format_maintenance_paid,
    format_operation_collect,
    format_operation_start,
    format_operations,
    format_ops_plan,
    format_reward_claim,
    format_rewards,
    format_rat_cleanup,
    format_status,
    format_wheel,
    format_wheel_spin,
)
from maintenance import collect_factories, TIER_ORDER
from mde_api import MydEmpireClient, MydEmpireAPIError, RateLimitedError
from daily_log import log_action
import daily_log

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)

_logs_dir = Path(__file__).resolve().parent / "logs"
try:
    _logs_dir.mkdir(parents=True, exist_ok=True)
except OSError as exc:  # noqa: BLE001
    print(f"[main] warning: cannot create logs dir {_logs_dir}: {exc}",
          file=sys.stderr)

try:
    _file_handler = RotatingFileHandler(
        _logs_dir / "bot.log",
        maxBytes=1_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
except OSError as exc:  # noqa: BLE001
    print(f"[main] warning: cannot open log file: {exc}", file=sys.stderr)
    _file_handler = None

logger = logging.getLogger("mde_bot")
logger.setLevel(logging.INFO)
if _file_handler is not None:
    logger.addHandler(_file_handler)

dp = Dispatcher()
api = MydEmpireClient()


def _snapshot(d: dict) -> tuple:
    keys = (
        "empBalance",
        "totalFactories",
        "activeFactories",
        "currentEpDay",
        "lifetimeEP",
        "totalClaimableEP",
        "smpBalance",
        "relicCount",
        "ratActivity",
    )
    return tuple(d.get(k) for k in keys)


async def _reply(message: Message, text: str) -> None:
    await message.answer(text, parse_mode=ParseMode.HTML)


async def _safe_reply(message: Message, text: str) -> None:
    if len(text) > 4096:
        text = text[:4000] + "\n... (truncated)"
    text = html.escape(text)
    await _reply(message, f"<pre>{text}</pre>")


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await _reply(
        message,
        f"Hello! I help you manage your MydEmpire account @{config.HIVE_USERNAME}.\n\n"
        "Commands:\n"
        "/status - all important info from the dashboard\n"
        "/lands - lands and slots summary\n"
        "/maint [days] - factories with maintenance expiring within N days (default 2)\n"
        "/paymaint [days] - auto-pay 7 days maintenance for due factories\n"
        "/check_lands - check maintenance & auto-pay due factories if balance positive\n"
        "/goods - goods claim status\n"
        "/claim - claim all factory goods\n"
        "/crate - open the free daily Imperial Supply Crate\n"
        "/plan_claim - check goods cooldown & schedule auto-claim\n"
        "/daily - run daily routine now (claim HIVE + check lands + goods)\n"
        "/global - global game stats and treasury health\n"
        "/rewards - claimable rewards and withdrawal status\n"
        "/claimhive - claim positive claimable HIVE balance\n"
        "/wheel - activity wheel status\n"
        "/wheel_spin - spin the wheel while spins are available\n"
        "/cleanup - check warehouse condition and clean if not Spotless\n"
        "/ops - empire operations\n"
        "/ops_start - start daily ops automation (LOCAL_SUPPLY x3, 4-7h gaps)\n"
        "/ops_status - current ops automation status\n"
        "/fulfillment - check factory fulfillment progress & plan claim\n"
        "/stats - gather statistics and write today's row to Google Sheets\n"
        "/mint - claim Imperial Mint EMP and pay its maintenance\n"
        "/publish - publish Daily Empire Report on Hive + claim reward\n"
        "/report - show today's activity report\n"
        "/help - this message",
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await cmd_start(message)


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        d = await api.dashboard(config.HIVE_USERNAME)
        text = format_status(config.HIVE_USERNAME, d)
    except MydEmpireAPIError as exc:
        await _reply(message, f"API error: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("status failed")
        await _reply(message, f"Failed to load status: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("global"))
async def cmd_global(message: Message) -> None:
    try:
        g = await api.global_stats()
        h = await api.global_health()
        text = format_global_stats(g, h)
    except Exception as exc:  # noqa: BLE001
        logger.exception("global failed")
        await _reply(message, f"Failed to load global stats: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("rewards"))
async def cmd_rewards(message: Message) -> None:
    try:
        r = await api.reward_summary(config.HIVE_USERNAME)
        text = format_rewards(r)
    except Exception as exc:  # noqa: BLE001
        logger.exception("rewards failed")
        await _reply(message, f"Failed to load rewards: {exc}")
        return
    await _safe_reply(message, text)


async def _claim_hive_text() -> str:
    """Claim the positive claimable HIVE balance.

    Returns a formatted result string.
    """
    r = await api.reward_summary(config.HIVE_USERNAME)
    amount = float(r.get("claimable_amount") or 0)
    if amount <= 0:
        return "No HIVE available to claim right now."
    d = await api.claim_rewards(config.HIVE_USERNAME)
    claimed = float(d.get("claimable_amount") or 0)
    log_action("HIVE reward claimed", emp=None, detail=f"{claimed} HIVE")
    return format_reward_claim(d)


@dp.message(Command("claimhive"))
async def cmd_claimhive(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _claim_hive_text()
    except MydEmpireAPIError as exc:
        await _reply(message, f"API error: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("claimhive failed")
        await _reply(message, f"Failed to claim HIVE: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("wheel"))
async def cmd_wheel(message: Message) -> None:
    try:
        w = await api.activity_wheel(config.HIVE_USERNAME)
        text = format_wheel(w)
    except Exception as exc:  # noqa: BLE001
        logger.exception("wheel failed")
        await _reply(message, f"Failed to load activity wheel: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("wheel_spin"))
async def cmd_wheel_spin(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _plan_wheel_text()
    except Exception as exc:  # noqa: BLE001
        logger.exception("wheel_spin failed")
        await _reply(message, f"Failed to spin wheel: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("cleanup"))
async def cmd_cleanup(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _plan_warehouse_clean()
    except Exception as exc:  # noqa: BLE001
        logger.exception("cleanup failed")
        await _reply(message, f"Failed to check/clean warehouse: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("ops"))
async def cmd_ops(message: Message) -> None:
    try:
        op = await api.empire_operations(config.HIVE_USERNAME)
        text = format_operations(op)
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops failed")
        await _reply(message, f"Failed to load empire operations: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("lands"))
async def cmd_lands(message: Message) -> None:
    try:
        o = await api.empire_overview(config.HIVE_USERNAME)
        text = format_lands(config.HIVE_USERNAME, o)
    except Exception as exc:  # noqa: BLE001
        logger.exception("lands failed")
        await _reply(message, f"Failed to load lands: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("maint"))
async def cmd_maint(message: Message) -> None:
    threshold = 2.0
    args = message.text.split()
    if len(args) > 1:
        try:
            threshold = float(args[1])
        except ValueError:
            pass
    try:
        o = await api.empire_overview(config.HIVE_USERNAME)
        factories = collect_factories(o)
        text = format_maintenance(
            config.HIVE_USERNAME, factories, threshold
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("maint failed")
        await _reply(message, f"Failed to check maintenance: {exc}")
        return
    await _safe_reply(message, text)


async def _pay_maintenance(factories: list, threshold: float) -> dict:
    """Pay 7 days maintenance for factories with days_left <= threshold.

    Returns a dict with counts and details for formatting.
    """
    low = sorted(
        [f for f in factories if f["days_left"] <= threshold],
        key=lambda f: f["days_left"],
    )

    paid = []
    failed = []
    for i, f in enumerate(low):
        if i > 0:
            await asyncio.sleep(
                random.uniform(
                    intervals.PAY_MAINT_DELAY_MIN,
                    intervals.PAY_MAINT_DELAY_MAX,
                )
            )

        result = None
        for attempt in range(intervals.PAY_MAINT_RETRIES):
            try:
                result = await api.factory_pay_maintenance(
                    config.HIVE_USERNAME, f["id"], 7
                )
                break
            except RateLimitedError:
                if attempt == intervals.PAY_MAINT_RETRIES - 1:
                    failed.append((f, "rate limited after retries"))
                    result = None
                    break
                wait = intervals.PAY_MAINT_RETRY_BASE_SECONDS * (
                    2 ** attempt
                )
                await asyncio.sleep(wait)
            except MydEmpireAPIError as exc:
                failed.append((f, str(exc)))
                result = None
                break
            except Exception as exc:  # noqa: BLE001
                failed.append((f, str(exc)))
                result = None
                break

        if result:
            if result.get("success"):
                paid.append((f, result))
                daily_log.log_action(
                    f"Maintenance paid #{f['id']} {f.get('factory_name', '?')}",
                    emp=-float(result.get("emp_spent") or 0),
                    detail=f"+{_int(result.get('days_added'))}d",
                )
            else:
                failed.append((f, result.get("error", "unknown error")))

    return {
        "total": len(factories),
        "due": len(low),
        "paid": paid,
        "failed": failed,
    }


def _format_pay_result(result: dict, title: str) -> str:
    lines = [
        title,
        f"Checked: {result['total']} | Due: {result['due']}",
        f"Paid: {len(result['paid'])} | Failed: {len(result['failed'])}",
    ]
    if result["paid"]:
        lines.append("")
        lines.append("Paid:")
        for f, r in result["paid"]:
            lines.append(
                f"  #{f['id']} {f.get('factory_name', '?')} "
                f"(+{_int(r.get('days_added'))}d, "
                f"{_num(r.get('emp_spent'))} EMP)"
            )
    if result["failed"]:
        lines.append("")
        lines.append("Failed:")
        for f, err in result["failed"]:
            lines.append(
                f"  #{f['id']} {f.get('factory_name', '?')}: {err}"
            )
    return "\n".join(lines)


async def _auto_upgrade_factories(overview: dict, balance: float) -> tuple[list, list, float]:
    """Upgrade factories that are ready, while balance covers the cost.

    Each factory upgrade is wrapped in its own try/except so one failure does
    not stop the remaining upgrades. Returns (upgraded, failed, remaining_balance).

    If an active season industry is set, factories of that industry are
    prioritized in the upgrade queue.
    """
    from maintenance import upgrade_ready

    candidates = upgrade_ready(overview)

    # Priority: if there's an active season industry, factories of that
    # industry are moved to the front of the queue (before tier sorting).
    season_industry = None
    try:
        season = await api.active_season()
        season_data = season.get("season")
        if season_data:
            season_industry = season_data.get("industry")
    except Exception:  # noqa: BLE001
        logger.exception("failed to fetch active season")
    if season_industry:
        def _priority(c):
            # 0 = matches season industry, 1 = otherwise; then by tier number
            industry_match = 0 if c.get("industry") == season_industry else 1
            return (industry_match, TIER_ORDER.get(c["tier"], 99))
        candidates.sort(key=_priority)
    if not candidates:
        return [], [], balance

    upgraded = []
    failed = []
    bal = balance
    for cand in candidates:
        if bal < cand["cost"]:
            break
        try:
            result = await api.factory_upgrade(
                config.HIVE_USERNAME, cand["factory_id"]
            )
        except RateLimitedError:
            failed.append((cand, "rate limited"))
            await asyncio.sleep(intervals.PAY_MAINT_RETRY_BASE_SECONDS)
            continue
        except Exception as exc:  # noqa: BLE001
            failed.append((cand, str(exc)))
            continue
        if result and result.get("success"):
            upgraded.append(cand)
            bal -= cand["cost"]
            log_action(
                f"Factory upgrade #{cand['factory_id']} {cand.get('tier')}->{cand.get('next_tier')}",
                emp=-float(cand["cost"]),
                detail=cand.get("factory_name", ""),
            )
            await asyncio.sleep(
                random.uniform(
                    intervals.PAY_MAINT_DELAY_MIN,
                    intervals.PAY_MAINT_DELAY_MAX,
                )
            )
        else:
            failed.append((cand, (result or {}).get("error", "unknown error")))
    return upgraded, failed, bal


def _format_upgrade_result(
    upgraded: list, failed: list, remaining_balance: float
) -> str:
    lines = ["=== Factory Auto-Upgrade ==="]
    lines.append(f"Upgraded: {len(upgraded)} | Failed: {len(failed)}")
    lines.append(f"Remaining EMP balance: {_num(remaining_balance)}")
    if upgraded:
        lines.append("")
        lines.append("Upgraded:")
        for f in upgraded:
            lines.append(
                f"  #{f['factory_id']} {f.get('factory_name', '?')} "
                f"{f.get('tier')} -> {f.get('next_tier')} "
                f"({_int(f.get('cost'))} EMP)"
            )
    if failed:
        lines.append("")
        lines.append("Failed:")
        for f, err in failed:
            lines.append(
                f"  #{f['factory_id']} {f.get('factory_name', '?')}: {err}"
            )
    return "\n".join(lines)


@dp.message(Command("paymaint"))
async def cmd_paymaint(message: Message) -> None:
    threshold = 2.0
    args = message.text.split()
    if len(args) > 1:
        try:
            threshold = float(args[1])
        except ValueError:
            pass
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        o = await api.empire_overview(config.HIVE_USERNAME)
        factories = collect_factories(o)
        result = await _pay_maintenance(factories, threshold)
        if not result["due"]:
            text = (
                f"No factories with <= {threshold:g} days left. "
                "Nothing to pay."
            )
            await _safe_reply(message, text)
            return
        text = _format_pay_result(result, "=== Auto Maintenance Pay ===")
    except MydEmpireAPIError as exc:
        await _reply(message, f"API error: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("paymaint failed")
        await _reply(message, f"Failed to pay maintenance: {exc}")
        return
    await _safe_reply(message, text)


async def _check_lands_text() -> str:
    """Check factory maintenance, auto-pay due factories, and auto-upgrade.

    Maintenance is paid first, then upgrade-ready factories are upgraded while
    the remaining EMP balance covers the cost. Returns a formatted report.
    """
    d = await api.dashboard(config.HIVE_USERNAME)
    balance = float(d.get("empBalance") or 0)
    o = await api.empire_overview(config.HIVE_USERNAME)
    factories = collect_factories(o)

    due = [f for f in factories if f["days_left"] <= 2.0]
    lines = [
        f"=== Check Lands: @{config.HIVE_USERNAME} ===",
        f"EMP balance: {_num(balance)}",
        f"Factories checked: {len(factories)}",
        f"Due within 2 days: {len(due)}",
    ]

    remaining = balance
    if due:
        if balance <= 0:
            lines.append("")
            lines.append(
                "Maintenance needed but EMP balance is not positive. "
                "Nothing paid."
            )
        else:
            lines.append("")
            lines.append(
                f"Balance positive, paying {len(due)} factory/factories..."
            )
            result = await _pay_maintenance(factories, 2.0)
            remaining = balance - sum(
                float(r.get("emp_spent") or 0) for _, r in result.get("paid", [])
            )
            lines.append(_format_pay_result(result, "=== Maintenance Auto-Pay ==="))
    else:
        lines.append("")
        lines.append("No factories due for maintenance. All good.")

    from maintenance import upgrade_ready

    candidates = upgrade_ready(o)
    if candidates:
        lines.append("")
        lines.append(
            f"Upgrade-ready factories: {len(candidates)} "
            f"(auto-upgrading while balance allows)..."
        )
        upgraded, failed, remaining = await _auto_upgrade_factories(o, remaining)
        lines.append(
            _format_upgrade_result(upgraded, failed, remaining)
        )
    else:
        lines.append("")
        lines.append("No upgrade-ready factories.")

    return "\n".join(lines)


@dp.message(Command("check_lands"))
async def cmd_check_lands(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _check_lands_text()
    except MydEmpireAPIError as exc:
        await _reply(message, f"API error: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("check_lands failed")
        await _reply(message, f"Failed to check lands: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("goods"))
async def cmd_goods(message: Message) -> None:
    try:
        p = await api.goods_preview(config.HIVE_USERNAME)
        text = format_goods_preview(p)
    except Exception as exc:  # noqa: BLE001
        logger.exception("goods failed")
        await _reply(message, f"Failed to load goods preview: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("claim"))
async def cmd_claim(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        p = await api.goods_preview(config.HIVE_USERNAME)
        if not p.get("playerClaimReady"):
            await _reply(
                message,
                "Goods are not ready to claim yet:\n"
                + format_goods_preview(p),
            )
            return
        d = await api.goods_claim(config.HIVE_USERNAME)
        text = format_goods_claim(d)
    except MydEmpireAPIError as exc:
        await _reply(message, f"API error: {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("claim failed")
        await _reply(message, f"Failed to claim goods: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("plan_claim"))
async def cmd_plan_claim(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _plan_goods_claim_text()
    except Exception as exc:  # noqa: BLE001
        logger.exception("plan_claim failed")
        await _reply(message, f"Failed to plan goods claim: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("crate"))
async def cmd_crate(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _plan_crate_text()
    except Exception as exc:  # noqa: BLE001
        logger.exception("crate failed")
        await _reply(message, f"Failed to check crate: {exc}")
        return
    await _safe_reply(message, text)


# ---------------------------------------------------------------------------
# Daily scheduled tasks: goods claim plan, HIVE claim, check lands.
# ---------------------------------------------------------------------------

_delayed_claim_task: asyncio.Task | None = None
_bot: Bot | None = None


async def _notify(text: str) -> None:
    """Send a background notification if a chat id is configured, else log."""
    chat_id = config.GOODS_CLAIM_NOTIFY_CHAT_ID
    if not chat_id or _bot is None:
        logger.info("[notify] %s", text[:500])
        return
    await _send_tg_message(chat_id, text)


async def _send_tg_message(chat_id: int, text: str) -> None:
    """Send a possibly long message to Telegram, splitting over 4000 chars."""
    if _bot is None:
        logger.info("[notify] %s", text[:500])
        return
    chunk = 3500
    parts = [text[i : i + chunk] for i in range(0, len(text), chunk)] or [text]
    try:
        for part in parts:
            await _bot.send_message(
                chat_id,
                f"<pre>{html.escape(part)}</pre>",
                parse_mode=ParseMode.HTML,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("notify failed: %s", exc)


async def _send_daily_report(text: str) -> None:
    """Send the full daily report to the configured report chat (or default)."""
    chat_id = config.DAILY_REPORT_CHAT_ID or config.GOODS_CLAIM_NOTIFY_CHAT_ID
    if not chat_id or _bot is None:
        logger.info("[daily report] %s", text[:500])
        return
    header = f"===== Daily Report {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n"
    await _send_tg_message(chat_id, header + text)


async def _plan_goods_claim_text() -> str:
    """Check goods claim status; claim if ready, else persist a planned time.

    The planned claim time is written to ``state.json`` so a restart cannot
    lose it. The scheduler loop reads that time and fires the claim exactly
    when due (no polling).
    """
    global _delayed_claim_task
    try:
        p = await api.goods_preview(config.HIVE_USERNAME)
    except MydEmpireAPIError as exc:
        if "Goods view is private" in str(exc):
            return "Goods claim: view is private — set to Public in Factory Goods → Inventory to enable auto-claim."
        raise
    if p.get("playerClaimReady"):
        d = await api.goods_claim(config.HIVE_USERNAME)
        text = format_goods_claim(d)
        if config.AUTO_REDEMPTION:
            try:
                text += "\n\n" + await _auto_redeem_goods()
            except Exception as exc:  # noqa: BLE001
                text += f"\n\nGoods redemption failed: {exc}"
                logger.exception("auto redeem failed")
        return text

    remaining = int(
        p.get("remainingGoodsClaimSeconds")
        or p.get("remaining_goods_claim_seconds")
        or 0
    )
    if remaining > 0:
        planned = (
            datetime.now().astimezone()
            + timedelta(seconds=remaining + intervals.GOODS_CLAIM_BUFFER_SECONDS)
        )
        scheduler.set_planned(_GOODS_STATE_KEY, planned)
        wait = (planned - datetime.now().astimezone()).total_seconds()
        scheduled_for = planned.strftime("%Y-%m-%d %H:%M:%S")
        if _delayed_claim_task is None or _delayed_claim_task.done():
            _delayed_claim_task = asyncio.create_task(
                _goods_claim_and_requeue(wait=wait)
            )
        return format_goods_claim_plan(
            {
                "playerClaimReady": False,
                "remainingGoodsClaimSeconds": remaining,
                "claimed": False,
                "scheduled_for": scheduled_for,
                "status": (
                    f"Auto-claim scheduled at {scheduled_for} "
                    f"(in {int(wait)}s, +{intervals.GOODS_CLAIM_BUFFER_SECONDS}s buffer)"
                ),
            }
        )
    scheduler.clear_planned(_GOODS_STATE_KEY)
    return format_goods_claim_plan(
        {
            "playerClaimReady": False,
            "remainingGoodsClaimSeconds": 0,
            "claimed": False,
            "status": "No active cycle / not ready.",
        }
    )


async def _auto_redeem_goods() -> str:
    """Bulk-redeem AVAILABLE goods on the inventory tab.

    Called after a goods claim when AUTO_REDEMPTION is enabled. Fetches the
    inventory, collects every AVAILABLE good id, and submits them all to the
    redemption burn endpoint in a single bulk call.

    Filtering based on AUTO_REDEMPTION_MODE:
    - ALL         : redeem everything
    - EXCEPT_TICKET_MINT : skip goods needed for Imperial Ticket Mint
    - NONE        : nothing redeemed (handled by AUTO_REDEMPTION flag)
    """
    inventory = await api.goods_inventory(config.HIVE_USERNAME)
    items = inventory.get("items") or []

    # Mapping quality to star count and level to rarity number
    QUALITY_STARS = {"STANDARD": 1, "FINE": 2, "SUPERIOR": 3}
    LEVEL_RARITY = {"ESSENTIAL": 1, "STANDARD": 2, "VALUE": 3, "PREMIUM": 4, "LUXURY": 5}
    TICKET_INDUSTRIES = {"FOOD", "TEXTILE", "PHARMA", "CHEMICAL", "SUPERMARKET"}

    def _is_ticket_mint_candidate(item: dict) -> bool:
        """Check if a good is needed for Imperial Ticket Mint."""
        quality = str(item.get("quality") or "").upper()
        level = str(item.get("product_level") or "").upper()
        industry = str(item.get("industry") or "").upper()
        stars = QUALITY_STARS.get(quality, 0)
        rarity = LEVEL_RARITY.get(level, 0)
        # R1 3★ from any of the 5 industries
        if rarity == 1 and stars >= 3 and industry in TICKET_INDUSTRIES:
            return True
        # R2 2★+ from any industry
        if rarity == 2 and stars >= 2:
            return True
        # R3 2★+ from any industry
        if rarity == 3 and stars >= 2:
            return True
        # R4+ 1★+ from any industry
        if rarity >= 4:
            return True
        return False

    if config.AUTO_REDEMPTION_MODE == "ALL":
        available = [
            item.get("id")
            for item in items
            if str(item.get("status") or "AVAILABLE").upper() == "AVAILABLE" and item.get("id")
        ]
        skipped = []
    elif config.AUTO_REDEMPTION_MODE == "EXCEPT_TICKET_MINT":
        available = []
        skipped = []
        for item in items:
            if str(item.get("status") or "AVAILABLE").upper() != "AVAILABLE" or not item.get("id"):
                continue
            iid = item.get("id")
            if _is_ticket_mint_candidate(item):
                skipped.append(iid)
            else:
                available.append(iid)
    else:  # NONE
        return "=== Goods Redemption ===\nAuto-redemption disabled."

    if not available and not skipped:
        return "=== Goods Redemption ===\nNo AVAILABLE goods to redeem."
    
    result = None
    if available:
        result = await api.goods_burn_redemption(
            config.HIVE_USERNAME, available
        )
        log_action(
            "Goods redeemed",
            emp=float(result.get("empReward") or 0) if result.get("empReward") is not None else None,
            detail=f"{len(available)} goods",
        )
    
    line = (
        "=== Goods Redemption ===\n"
        f"Redeemed: {len(available)} goods"
    )
    if skipped:
        line += f"\nSkipped: {len(skipped)} goods reserved for Imperial Ticket Mint"
    if result and result.get("message"):
        line += f"\n{result['message']}"
    if result and result.get("empReward") is not None:
        line += f"\nEMP reward: {_num(result.get('empReward'))}"
    if result and result.get("productValue") is not None:
        line += f"\nProduct value: {_num(result.get('productValue'))}"
    return line


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def _crate_status() -> dict:
    """Determine current crate availability from history + cooldown rules.

    Supports up to CRATE_MAX_CLAIMS_PER_DAY claims per day,
    with a minimum 3-hour cooldown between claims.
    Before each claim, checks if 15 EMP is available on balance.
    """
    hist = await api.crate_history(config.HIVE_USERNAME)
    entries = hist.get("history") or []
    now = datetime.now(tz=datetime.utcnow().astimezone().tzinfo)

    opened_today = 0
    last_opened = None
    for e in entries:
        ts = _parse_iso(e.get("created_at"))
        if not ts:
            continue
        if last_opened is None or ts > last_opened:
            last_opened = ts
        opened_utc = ts.astimezone(tz=datetime.utcnow().astimezone().tzinfo)
        if opened_utc.date() == now.date():
            opened_today += 1

    # Check minimum cooldown between claims
    cooldown_until = None
    if last_opened is not None:
        cooldown_until = last_opened + timedelta(
            seconds=intervals.CRATE_COOLDOWN_SECONDS
        )

    # Count how many claims are possible today respecting cooldown
    # A claim is possible if: opened_today < max AND (no cooldown or cooldown elapsed)
    can_open_more = opened_today < config.CRATE_MAX_CLAIMS_PER_DAY
    cooldown_elapsed = True
    if last_opened is not None and cooldown_until is not None:
        cooldown_elapsed = now >= cooldown_until

    # Check 15 EMP balance before allowing a claim
    emp_balance = 0.0
    try:
        d = await api.dashboard(config.HIVE_USERNAME)
        emp_balance = float(d.get("empBalance") or 0)
    except Exception:  # noqa: BLE001
        pass
    has_enough_emp = emp_balance >= 15

    # Determine if we can open a crate
    can_open = can_open_more and cooldown_elapsed and has_enough_emp
    opened_today = min(opened_today, config.CRATE_MAX_CLAIMS_PER_DAY)

    # Determine cooldown status
    cooldown_remaining = None
    if last_opened is not None and cooldown_until is not None:
        if now < cooldown_until:
            cooldown_remaining = str(cooldown_until - now)

    return {
        "can_open": can_open,
        "opened_today": opened_today,
        "max_today": config.CRATE_MAX_CLAIMS_PER_DAY,
        "eligible": float(d.get("globalSharePercent") or 0) >= 1.5,
        "last_opened": last_opened,
        "cooldown_remaining": cooldown_remaining,
        "emp_balance": emp_balance,
    }


async def _open_crates_up_to_daily_max() -> list[str]:
    """Open crates while the daily limit and cooldown allow.

    If the balance is short of the 15 EMP crate fee but an empire operation is
    running that will finish soon, waits for its completion (reward brings
    EMP back) and retries instead of giving up.
    """
    lines = []
    while True:
        status = await _crate_status()
        if not status["can_open"]:
            if status["opened_today"] >= config.CRATE_MAX_CLAIMS_PER_DAY:
                lines.append(
                    f"Daily limit reached ({config.CRATE_MAX_CLAIMS_PER_DAY} crates)."
                )
            elif status["cooldown_remaining"]:
                lines.append(f"On cooldown: {status['cooldown_remaining']}")
            elif not status["eligible"]:
                lines.append("Not eligible (need globalShare >= 1.5%).")
            elif status["emp_balance"] < 15:
                # Short on EMP: if an operation is about to finish, wait for its
                # reward instead of skipping today's crates.
                try:
                    ops = await api.empire_operations(config.HIVE_USERNAME)
                    active = ops.get("activeOperation")
                except Exception:  # noqa: BLE001
                    active = None
                ends = _parse_iso((active or {}).get("ends_at"))
                if ends:
                    wait = max(
                        0.0,
                        (ends - datetime.now(tz=datetime.utcnow().astimezone().tzinfo)).total_seconds(),
                    )
                    if wait <= 6 * 3600:
                        lines.append(
                            f"Balance {_num(status['emp_balance'])} EMP < 15. "
                            f"Waiting {wait / 60:.0f}m for operation reward..."
                        )
                        try:
                            collected = await _wait_then_collect()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("crate op-wait collect failed: %s", exc)
                            collected = None
                        if collected:
                            lines.append(format_operation_collect(collected))
                            await _notify(format_operation_collect(collected))
                            continue
                lines.append(
                    f"Balance {_num(status['emp_balance'])} EMP < 15 — skipping crate."
                )
            else:
                lines.append("Not available.")
            break
        try:
            d = await api.open_imperial_crate(config.HIVE_USERNAME)
        except RateLimitedError:
            lines.append("Crate rate limited, stopping.")
            break
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Crate open failed: {exc}")
            break
        reward = d.get("reward") or {}
        emp = (
            d.get("emp_change")
            or d.get("reward_change")
            or reward.get("reward_amount")
            or reward.get("emp_change")
        )
        rtype = d.get("reward_type") or reward.get("reward_type") or "reward"
        rvalue = d.get("reward_value") or reward.get("reward_value") or ""
        label = rvalue or f"{rtype} x{emp}" if emp is not None else str(rtype)
        try:
            emp_f = float(emp) if emp is not None else None
        except (TypeError, ValueError):
            emp_f = None
        if emp_f is not None:
            lines.append(f"Opened crate: reward {_num(emp_f)} EMP ({label})")
        else:
            lines.append(f"Opened crate: {label}")
        log_action(
            "Imperial crate opened",
            emp=emp_f,
            detail=str(label),
        )
        await asyncio.sleep(3)
    return lines


async def _plan_crate_text() -> str:
    """Open crates up to the daily max, waiting for op rewards when short on EMP."""
    lines = await _open_crates_up_to_daily_max()
    return "=== Imperial Supply Crate ===\n" + "\n".join(lines)


async def _check_crate_auto() -> None:
    """Open crates whenever one becomes available (throttled via state.json).

    Runs from the 5-minute scheduler loop so crates 2-4 (3h cooldown each)
    are opened during the day instead of only at the 02:00 run.
    """
    last = scheduler.get_planned("crate_last_check")
    now = datetime.now().astimezone()
    if last is not None and (now - last).total_seconds() < intervals.CRATE_CHECK_INTERVAL_SECONDS:
        return
    scheduler.set_planned("crate_last_check", now)
    status = await _crate_status()
    if not status["can_open"]:
        return
    lines = await _open_crates_up_to_daily_max()
    if lines:
        await _notify("Auto-crate:\n" + "\n".join(lines))


# ---------------------------------------------------------------------------
# Empire operations automation: run up to OPS_PER_DAY operations, starting each
# after a random 4-7h gap, collecting each when ready.
# ---------------------------------------------------------------------------

_ops_task: asyncio.Task | None = None


def _is_today(ts: str, tz=None) -> bool:
    dt = _parse_iso(ts)
    if not dt:
        return False
    ref = dt.astimezone(tz or datetime.now().astimezone().tzinfo)
    return ref.date() == datetime.now().date()


async def _count_ops_started_today() -> int:
    """Count operations of config.OPS_TYPE started today."""
    d = await api.empire_operations(config.HIVE_USERNAME)
    hist = d.get("history") or []
    return sum(
        1
        for h in hist
        if h.get("operation_type") == config.OPS_TYPE and _is_today(h.get("started_at"))
    )


async def _collect_active_if_ready() -> dict | None:
    """If an active operation is ready (ended), collect it. Returns result or None."""
    d = await api.empire_operations(config.HIVE_USERNAME)
    active = d.get("activeOperation")
    if not active:
        return None
    ends = _parse_iso(active.get("ends_at"))
    now = datetime.now(tz=datetime.utcnow().astimezone().tzinfo)
    if ends and now >= ends:
        return await api.collect_operation(config.HIVE_USERNAME, active["id"])
    return None


async def _wait_then_collect() -> dict | None:
    """Poll until the running op is ready, then collect it."""
    for _ in range(int(24 * 60 * 60 / intervals.OPS_POLL_INTERVAL_SECONDS)):
        res = await _collect_active_if_ready()
        if res is not None:
            return res
        await asyncio.sleep(intervals.OPS_POLL_INTERVAL_SECONDS)
    return None


async def _start_one_operation() -> dict:
    return await api.start_operation(
        config.HIVE_USERNAME, config.OPS_TYPE, config.OPS_BUDGET
    )


async def _run_ops_automation(quiet: bool = False) -> list[str]:
    """Run the full ops cycle: start up to OPS_PER_DAY ops, each separated by
    a random 4-7h gap, collecting each when it finishes.

    Returns a list of human-readable lines describing what happened.
    """
    global _ops_task
    lines = []

    # Account for the pre-existing active op (if any) and any already-started today.
    data = await api.empire_operations(config.HIVE_USERNAME)
    active = data.get("activeOperation")
    started = await _count_ops_started_today()
    remaining = config.OPS_PER_DAY - started
    lines.append(
        f"Ops started today: {started}/{config.OPS_PER_DAY} "
        f"({config.OPS_TYPE})"
    )
    if remaining <= 0:
        lines.append("Daily limit reached. Nothing to do.")
        _ops_task = None
        return lines

    # If there is already an active running op, do not start a new one;
    # wait for it to finish and collect it first.
    if active and active.get("operation_type") == config.OPS_TYPE:
        lines.append(
            f"Existing {config.OPS_TYPE} running (id {active['id']}). "
            "Waiting to collect it first."
        )
        collected = await _wait_then_collect()
        if collected:
            lines.append(format_operation_collect(collected))
            await _notify(format_operation_collect(collected))
            log_action(
                f"Operation collected {config.OPS_TYPE}",
                emp=float(collected.get("empReward") or collected.get("reward") or 0),
            )
        else:
            lines.append("Failed to collect the existing operation (timeout).")
            _ops_task = None
            return lines

    for _ in range(remaining):
        await _start_one_operation()
        lines.append(
            f"Started {config.OPS_TYPE} (budget {config.OPS_BUDGET} EMP)."
        )
        await _notify(
            f"Started {config.OPS_TYPE} (budget {config.OPS_BUDGET} EMP)."
        )
        log_action(
            f"Operation started {config.OPS_TYPE}",
            emp=-float(config.OPS_BUDGET),
        )

        collected = await _wait_then_collect()
        if collected:
            lines.append(format_operation_collect(collected))
            await _notify(format_operation_collect(collected))
            log_action(
                f"Operation collected {config.OPS_TYPE}",
                emp=float(collected.get("empReward") or collected.get("reward") or 0),
            )
        else:
            lines.append("Failed to collect operation (timeout).")
            break

        # gap before next start
        gap = random.uniform(4.0, 7.0) * 3600
        lines.append(f"Next operation in {gap / 3600:.1f}h.")
        await asyncio.sleep(gap)

    _ops_task = None
    return lines


async def _kickoff_ops_automation() -> str:
    """Start the ops automation as a background task if not already running."""
    global _ops_task
    if _ops_task is not None and not _ops_task.done():
        return "Operations automation already running."
    started = await _count_ops_started_today()
    remaining = config.OPS_PER_DAY - started
    if remaining <= 0:
        return (
            f"Daily limit reached: {started}/{config.OPS_PER_DAY} "
            f"{config.OPS_TYPE} ops already started today."
        )
    _ops_task = asyncio.create_task(_run_ops_automation())
    return (
        f"Operations automation started: {remaining} more {config.OPS_TYPE} "
        f"ops today (budget {config.OPS_BUDGET} EMP each), "
        f"4-7h gaps, collect when ready."
    )


# ---------------------------------------------------------------------------
# Factory fulfillment automation: check progress, estimate completion, claim
# when 100% (+ buffer), then start a new fulfillment.
# ---------------------------------------------------------------------------

_fulfillment_claim_task: asyncio.Task | None = None


async def _estimate_fulfillment_completion(active: dict, progress: dict) -> datetime | None:
    """Estimate when the active fulfillment will reach 100%.

    Uses points/sec based on elapsed time, falling back to the API's
    cycle_ends_at / elapsed_seconds when available.
    """
    now = datetime.now(tz=datetime.utcnow().astimezone().tzinfo)
    pct = float(progress.get("percent") or 0)
    if pct >= 100:
        return now
    target = float(active.get("target_points") or progress.get("target") or 0)
    current = float(progress.get("progress") or 0)
    if target <= 0 or current >= target:
        return None

    # Estimate rate from elapsed time if progress is > 0.
    elapsed = float(active.get("elapsed_seconds") or 0)
    if elapsed > 0 and current > 0:
        rate_per_sec = current / elapsed
        remaining = target - current
        if rate_per_sec > 0:
            return now + timedelta(seconds=remaining / rate_per_sec)

    # Fall back to cycle end timestamp.
    ends = _parse_iso(active.get("cycle_ends_at"))
    if ends:
        return ends.astimezone(tz=datetime.utcnow().astimezone().tzinfo)
    return None


def _pick_fulfillment_industry(d: dict) -> str | None:
    """Pick the fastest-completing industry for a new fulfillment.

    The fulfillment target scales with global EP, so the same target completes
    in proportion to the industry's EP/day — picking the strongest producing
    industry finishes ~2x faster than the weakest, for the same reward.
    Restricted to industries still eligible in the rotation when the rotation
    has remaining industries.
    """
    rotation = d.get("rotationStatus") or {}
    remaining = rotation.get("remainingIndustries") or []
    producing = d.get("producingIndustries") or []
    industry_ep = d.get("industryEP") or {}

    def _ep(name: str) -> float:
        return float(industry_ep.get(name) or 0)

    if remaining:
        return max(remaining, key=_ep)
    if producing:
        return max(producing, key=_ep)
    return None


async def _delayed_fulfillment_claim(wait: float) -> None:
    """Wait until the estimated completion, poll to 100%, then claim and start a new fulfillment.

    After claiming, waits 10 seconds (FULFILLMENT_RESTART_DELAY_SECONDS) before
    attempting to start a new fulfillment. If a cooldown is active, plans waiting
    time until the cooldown ends.
    """
    global _fulfillment_claim_task
    try:
        await asyncio.sleep(wait)
        for _ in range(int(48 * 3600 / intervals.OPS_POLL_INTERVAL_SECONDS)):
            d = await api.factory_fulfillment(config.HIVE_USERNAME)
            progress = d.get("activeProgress") or {}
            if progress.get("isTargetComplete") or (
                float(progress.get("percent") or 0) >= 100
            ):
                claim = await api.factory_fulfillment_claim(config.HIVE_USERNAME)
                outcome = claim.get("outcome") or claim.get("claimResult") or {}
                log_action(
                    "Fulfillment claimed",
                    emp=float(outcome.get("finalEmpReward") or 0),
                )
                await _notify(
                    "Fulfillment claimed:\n" + format_fulfillment_claim(claim)
                )
                # Wait 10 seconds before starting new fulfillment
                await asyncio.sleep(intervals.FULFILLMENT_RESTART_DELAY_SECONDS)
                try:
                    started = await _start_fulfillment()
                except Exception as exc:  # noqa: BLE001
                    logger.exception("auto-start fulfillment failed")
                    await _notify(f"Auto-start fulfillment failed: {exc}")
                    return
                if started and "Cannot start" not in started:
                    # _start_fulfillment already notified
                    pass
                elif started:
                    await _notify(started)
                return
            await asyncio.sleep(intervals.OPS_POLL_INTERVAL_SECONDS)
        await _notify("Fulfillment not ready after polling. Skipped.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("delayed fulfillment claim failed")
        await _notify(f"Fulfillment auto-claim failed: {exc}")
    finally:
        _fulfillment_claim_task = None


async def _fulfillment_status_text() -> str:
    """Check current fulfillment status and return a formatted summary.

    Includes: active fulfillment info, progress, time until 100%,
    cooldown status, and whether a new fulfillment is scheduled.
    """
    try:
        d = await api.factory_fulfillment(config.HIVE_USERNAME)
        active = d.get("activeFulfillment") or {}
        progress = d.get("activeProgress") or {}
        lines = ["=== Factory Fulfillment ==="]

        if not active:
            lines.append("No active fulfillment.")
            cooldown = d.get("fulfillmentCooldown") or {}
            if cooldown.get("active"):
                co_end = cooldown.get("cooldownEndsAt")
                if co_end:
                    lines.append(f"Cooldown until {co_end}.")
                else:
                    lines.append("Cooldown active, time unknown.")
            else:
                lines.append("No cooldown. Use /fulfillment to start a new one.")
            return "\n".join(lines)

        pct = float(progress.get("percent") or 0)
        complete = progress.get("isTargetComplete") or pct >= 100

        lines.append(f"Active: {active.get('fulfillment_type')} ({active.get('industry')})")
        lines.append(f"Progress: {_int(progress.get('progress'))} / {_int(progress.get('target'))} ({_num(pct)}%)")
        
        if complete:
            lines.append("Target reached! Claimed and new fulfillment scheduled.")
        else:
            # Estimate remaining time
            elapsed = float(active.get("elapsed_seconds") or 0)
            current = float(progress.get("progress") or 0)
            target = float(active.get("target_points") or progress.get("target") or 0)
            if target > 0 and current < target and elapsed > 0:
                rate = current / elapsed
                remaining = target - current
                remaining_secs = max(0, remaining / rate)
                # Format minutes and hours
                mins = int(remaining_secs // 60)
                hours = int(remaining_secs // 3600)
                if hours > 0:
                    lines.append(f"Estimated 100% in {hours}h {mins%60}m")
                else:
                    lines.append(f"Estimated 100% in {mins}m")
            else:
                lines.append("Cannot estimate completion time.")
        
        # Check cooldown for new fulfillment
        cooldown = d.get("fulfillmentCooldown") or {}
        if cooldown.get("active"):
            lines.append(
                f"Cooldown until {d.get('fulfillmentCooldown', {}).get('cooldownEndsAt', 'unknown')}. "
                "New fulfillment will start after cooldown."
            )
        else:
            lines.append("No cooldown. New fulfillment can be started.")

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.exception("fulfillment status check failed")
        return f"Fulfillment status check failed: {exc}"


async def _plan_fulfillment_text() -> str:
    """Check fulfillment progress; claim if 100%, else plan a delayed claim.

    Runs daily at 02:00: if progress is estimated to reach 100% later, the
    claim is scheduled at that time + 2s buffer.
    """
    global _fulfillment_claim_task
    d = await api.factory_fulfillment(config.HIVE_USERNAME)
    active = d.get("activeFulfillment") or {}
    progress = d.get("activeProgress") or {}
    claimed = d.get("lastFulfillment") or {}

    if not active:
        # Nothing active. If cooldown allows, start a new fulfillment.
        cooldown = d.get("fulfillmentCooldown") or {}
        if cooldown.get("active"):
            return (
                format_fulfillment_status(d)
                + "\n\nNo active fulfillment. "
                f"Cooldown until {_fmt_iso(cooldown.get('cooldownEndsAt'))}."
            )
        started = await _start_fulfillment(d)
        if started:
            fresh = await api.factory_fulfillment(config.HIVE_USERNAME)
            return format_fulfillment_status(fresh) + "\n\n" + started
        return format_fulfillment_status(d) + "\n\nNo active fulfillment."

    pct = float(progress.get("percent") or 0)
    complete = progress.get("isTargetComplete") or pct >= 100

    if complete:
        # Claim now (100% reached), then start a new fulfillment if possible.
        claim = await api.factory_fulfillment_claim(config.HIVE_USERNAME)
        outcome = claim.get("outcome") or claim.get("claimResult") or {}
        log_action(
            "Fulfillment claimed",
            emp=float(outcome.get("finalEmpReward") or 0),
        )
        await _notify(
            "Fulfillment claimed:\n" + format_fulfillment_claim(claim)
        )
        parts = [
            format_fulfillment_status(d),
            format_fulfillment_claim(claim),
        ]
        started = await _start_fulfillment()
        if started:
            parts.append(started)
        return "\n\n".join(parts)

    # Estimate completion and schedule delayed claim at +2s buffer.
    est = await _estimate_fulfillment_completion(active, progress)
    if est is None:
        return format_fulfillment_status(d) + "\n\nCannot estimate completion."
    wait = max(0.0, (est - datetime.now(tz=datetime.utcnow().astimezone().tzinfo)).total_seconds())
    wait += config.FULFILLMENT_CLAIM_BUFFER_SECONDS
    scheduled_for = (datetime.now() + timedelta(seconds=wait)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    if _fulfillment_claim_task is None or _fulfillment_claim_task.done():
        _fulfillment_claim_task = asyncio.create_task(_delayed_fulfillment_claim(wait))
    lines = [
        "=== Factory Fulfillment ===",
        f"Active: {active.get('fulfillment_type')} ({active.get('industry')})",
        f"Progress: {_int(progress.get('progress'))} / {_int(progress.get('target'))} "
        f"({_num(pct)}%)",
        f"Estimated 100%: {est.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Auto-claim scheduled at: {scheduled_for} (+{config.FULFILLMENT_CLAIM_BUFFER_SECONDS}s)",
    ]
    return "\n".join(lines)


def _fmt_iso(value: str | None) -> str:
    """Format an ISO timestamp for display; return raw value if unparsable."""
    if not value:
        return "n/a"
    parsed = _parse_iso(value)
    if parsed:
        return parsed.strftime("%Y-%m-%d %H:%M")
    return str(value)


async def _start_fulfillment(prior: dict | None = None) -> str | None:
    """Start a new fulfillment of the configured type.

    Picks the first remaining industry from the rotation (falling back to the
    first producing industry). Returns a formatted start message, or None if
    nothing can be started.
    """
    if prior is None:
        prior = await api.factory_fulfillment(config.HIVE_USERNAME)
    if prior.get("activeFulfillment"):
        return None
    cooldown = prior.get("fulfillmentCooldown") or {}
    if cooldown.get("active"):
        return (
            "Cannot start new fulfillment: cooldown until "
            f"{_fmt_iso(cooldown.get('cooldownEndsAt'))}."
        )
    industry = _pick_fulfillment_industry(prior)
    if not industry:
        return "Cannot start new fulfillment: no producing industries."
    await asyncio.sleep(intervals.FULFILLMENT_RESTART_DELAY_SECONDS)
    started = await api.factory_fulfillment_start(
        config.HIVE_USERNAME,
        config.FULFILLMENT_TYPE,
        industry,
    )
    text = format_fulfillment_start(started)
    await _notify("Fulfillment started:\n" + text)
    return text


async def _plan_wheel_text() -> str:
    """Check activity wheel; spin while available spins remain.

    Spins cost AP. The wheel reports availableSpins; when positive, we spin
    (re-checking after each spin in case of rewards granting more AP).
    """
    w = await api.activity_wheel(config.HIVE_USERNAME)
    spins = int(w.get("availableSpins") or 0)
    ap = int(w.get("currentAP") or 0)
    cost = int(w.get("spinCostAP") or 50)
    if spins <= 0:
        return (
            f"=== Activity Wheel ===\nNo spins available (AP {ap}, "
            f"cost {cost} AP each)."
        )
    lines = [
        f"=== Activity Wheel ===\nAvailable spins: {spins} (AP {ap}, "
        f"cost {cost} AP each). Spinning now...",
        "",
    ]
    spun = 0
    while True:
        w = await api.activity_wheel(config.HIVE_USERNAME)
        spins = int(w.get("availableSpins") or 0)
        ap = int(w.get("currentAP") or 0)
        if spins <= 0:
            break
        if ap < cost:
            break
        result = await api.activity_wheel_spin(config.HIVE_USERNAME)
        spun += 1
        reward = result.get("reward") or {}
        label = (
            reward.get("reward_label")
            or f"{reward.get('reward_type')} x{reward.get('reward_amount')}"
            or "unknown"
        )
        lines.append(f"Spin {spun}: {label}")
        emp_val = (
            float(reward.get("reward_amount"))
            if str(reward.get("reward_type") or "").upper() == "EMP"
            and reward.get("reward_amount") is not None
            else None
        )
        log_action("Wheel spin", emp=emp_val, detail=str(label))
        if not result.get("success"):
            break
        await asyncio.sleep(2)
    lines.append("")
    lines.append(f"Total spins this run: {spun}")
    await _notify("\n".join(lines))
    return "\n".join(lines)


async def _plan_warehouse_clean() -> str:
    """Check warehouse condition; launch cleanup unless Spotless.

    Condition levels: Spotless (clean), Clean, Orderly, Cluttered, Infested.
    Any state other than Spotless triggers the cleanup crew.
    """
    d = await api.dashboard(config.HIVE_USERNAME)
    condition = str(d.get("warehouseCondition") or "").strip().lower()
    if condition == "spotless":
        return (
            f"=== Warehouse ===\nCondition: Spotless — "
            "no cleanup needed."
        )
    cost = d.get("ratCleanupCost")
    cost_txt = f" (cost {_num(cost)} EMP)" if cost else ""
    result = await api.rat_cleanup(config.HIVE_USERNAME)
    log_action(
        "Warehouse cleanup",
        emp=-float(result.get("empSpent") or 0) if result.get("empSpent") is not None else None,
        detail=f"smp +{result.get('smpReward')}",
    )
    cleanup = format_rat_cleanup(result).splitlines()
    body = "\n".join(cleanup[1:]) if len(cleanup) > 1 else ""
    text = (
        f"=== Warehouse ===\nCondition: {condition}{cost_txt}\n{body}"
    ).strip()
    await _notify(text)
    return text


async def _mint_text() -> str:
    """Claim stored EMP from Imperial Mint lands and pay maintenance when due.

    Imperial Mint factories live in `land.special_industries` with
    industry_type IMPERIAL_MINT. Claims stored_emp; when maintenance has
    expired, pays 10 EMP to activate for another 7 days.
    """
    from datetime import timezone as _tz

    o = await api.empire_overview(config.HIVE_USERNAME)
    lines = ["=== Imperial Mint ==="]
    acted = False
    for land in o.get("lands") or []:
        for si in land.get("special_industries") or []:
            if str(si.get("industry_type") or "").upper() != "IMPERIAL_MINT":
                continue
            sid = si.get("id")
            stored = float(si.get("stored_emp") or 0)
            ends = _parse_iso(si.get("maintenance_ends_at"))
            now = datetime.now(_tz.utc)
            expired = ends is None or now >= ends
            if stored > 0:
                try:
                    r = await api.imperial_mint_claim(config.HIVE_USERNAME, sid)
                    emp_claimed = float(r.get("emp_claimed") or stored)
                    lines.append(
                        f"Claimed {_num(emp_claimed)} EMP from mint #{sid} "
                        f"(land {land.get('land_id')})"
                    )
                    log_action("Imperial Mint EMP claimed", emp=emp_claimed)
                    acted = True
                except Exception as exc:  # noqa: BLE001
                    lines.append(f"Mint claim #{sid} failed: {exc}")
            if expired:
                d = await api.dashboard(config.HIVE_USERNAME)
                bal = float(d.get("empBalance") or 0)
                if bal < 10:
                    lines.append(
                        f"Maintenance due on mint #{sid} but balance too low "
                        f"({_num(bal)} EMP, need 10)."
                    )
                else:
                    try:
                        await api.imperial_mint_maintenance(
                            config.HIVE_USERNAME, sid
                        )
                        lines.append(
                            f"Maintenance paid for mint #{sid} (10 EMP, +7d)"
                        )
                        log_action(
                            "Imperial Mint maintenance", emp=-10.0,
                            detail=f"mint #{sid}",
                        )
                        acted = True
                    except Exception as exc:  # noqa: BLE001
                        lines.append(f"Mint maintenance #{sid} failed: {exc}")
            else:
                days_left = (
                    (ends - now).total_seconds() / 86400 if ends else None
                )
                if not stored:
                    lines.append(
                        f"Mint #{sid}: no stored EMP, maintenance ok "
                        f"({days_left:.1f}d left)" if days_left is not None else
                        f"Mint #{sid}: no stored EMP"
                    )
    if not acted and len(lines) == 1:
        lines.append("Nothing to claim or pay.")
    return "\n".join(lines)


@dp.message(Command("mint"))
async def cmd_mint(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _mint_text()
    except Exception as exc:  # noqa: BLE001
        logger.exception("mint failed")
        await _reply(message, f"Failed to run imperial mint tasks: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("publish"))
async def cmd_publish(message: Message) -> None:
    """Publish the Daily Empire Report on Hive and claim the reward."""
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        from hive_publish import publish_daily_report

        text = await publish_daily_report(api, config.HIVE_USERNAME)
    except Exception as exc:  # noqa: BLE001
        logger.exception("publish failed")
        await _reply(message, f"Failed to publish report: {exc}")
        return
    await _safe_reply(message, text)


async def _run_daily_tasks_text():
    """Run the daily routine: claim HIVE, check lands, goods, crate, ops."""
    parts = []
    try:
        parts.append(await _claim_hive_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"HIVE claim failed: {exc}")
    try:
        parts.append(await _check_lands_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Check lands failed: {exc}")
    try:
        parts.append(await _plan_goods_claim_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Goods claim plan failed: {exc}")
    try:
        parts.append(await _plan_crate_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Crate failed: {exc}")
    try:
        parts.append(await _plan_fulfillment_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Fulfillment plan failed: {exc}")
    try:
        parts.append(await _plan_warehouse_clean())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Warehouse check failed: {exc}")
    try:
        parts.append(await _plan_wheel_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Wheel plan failed: {exc}")
    try:
        parts.append(await _kickoff_ops_automation())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Ops automation failed: {exc}")
    try:
        parts.append(await _leaderboard_positions_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Leaderboard check failed: {exc}")
    try:
        parts.append(await _mint_text())
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Imperial Mint failed: {exc}")
    try:
        from hive_publish import publish_daily_report

        parts.append(await publish_daily_report(api, config.HIVE_USERNAME))
    except Exception as exc:  # noqa: BLE001
        parts.append(f"Hive publish failed: {exc}")
    return "\n\n".join(parts)


async def _leaderboard_positions_text() -> str:
    """Gather leaderboard positions (emperor, season, redemption)."""
    from leaderboard import gather_leaderboard_report

    health = await api.global_health()
    reward_pool = (health.get("treasury") or {}).get("rewardPool")
    return await gather_leaderboard_report(
        api, config.HIVE_USERNAME, reward_pool
    )


@dp.message(Command("ops_start"))
async def cmd_ops_start(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _kickoff_ops_automation()
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops_start failed")
        await _reply(message, f"Failed to start ops automation: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("ops_status"))
async def cmd_ops_status(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        started = await _count_ops_started_today()
        running = _ops_task is not None and not _ops_task.done()
        text = format_ops_plan(
            {
                "Ops type": config.OPS_TYPE,
                "Started today": f"{started}/{config.OPS_PER_DAY}",
                "Automation running": "yes" if running else "no",
                "Budget per op": f"{config.OPS_BUDGET} EMP",
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops_status failed")
        await _reply(message, f"Failed to get ops status: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("fulfillment"))
async def cmd_fulfillment(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _plan_fulfillment_text()
    except Exception as exc:  # noqa: BLE001
        logger.exception("fulfillment failed")
        await _reply(message, f"Failed to check fulfillment: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Gather all statistics and write today's row to the Google Sheet."""
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _run_stats_to_sheet()
    except Exception as exc:  # noqa: BLE001
        logger.exception("stats failed")
        await _reply(message, f"Failed to gather statistics: {exc}")
        return
    await _safe_reply(message, text or "Statistics gathering is disabled.")


async def _run_stats_to_sheet() -> str:
    """Gather all statistics and write today's row to the Google Sheet.

    Enabled/disabled via config.STATS_ENABLED (default on). Returns a
    formatted summary (empty if disabled); callers decide how to send it.
    """
    from stats import run_stats_task

    stats = await run_stats_task(api)
    if not stats:
        return ""
    return "=== Statistics ===\n" + "\n".join(
        f"{k}: {v}" for k, v in stats.items()
    )


async def _daily_tasks_loop() -> None:
    """Run the daily actions every day at config.GOODS_CLAIM_CRON_TIME (02:00)."""
    while True:
        now = datetime.now()
        try:
            hh, mm = (int(x) for x in config.GOODS_CLAIM_CRON_TIME.split(":"))
        except (ValueError, AttributeError):
            hh, mm = 2, 0
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            d = await api.dashboard(config.HIVE_USERNAME)
            daily_log.save_asset_snapshot(d)
            await _run_daily_tasks_text()
        except Exception as exc:  # noqa: BLE001
            logger.exception("daily tasks run failed")
            daily_log.log_action("Daily tasks failed", detail=str(exc))
            await _notify(f"Daily tasks failed: {exc or type(exc).__name__}")
        # Safety net: if the scheduler missed a due goods claim, catch up now.
        try:
            planned = scheduler.get_planned(_GOODS_STATE_KEY)
            if planned is not None and planned <= datetime.now().astimezone():
                await _run_goods_claim()
        except Exception as exc:  # noqa: BLE001
            logger.warning("goods claim safety net failed: %s", exc)
        try:
            text = await _run_stats_to_sheet()
            if text:
                await _notify(text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("stats to sheet failed")
            await _notify(f"Stats to sheet failed: {exc or type(exc).__name__}")


async def _evening_report_loop() -> None:
    """Build and send the daily activity report at config.DAILY_LOG_TIME (23:58)."""
    while True:
        try:
            eh, em = (int(x) for x in config.DAILY_LOG_TIME.split(":"))
        except (ValueError, AttributeError):
            eh, em = 23, 58
        now = datetime.now()
        report_target = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        if report_target <= now:
            report_target += timedelta(days=1)
        await asyncio.sleep((report_target - now).total_seconds())
        lb = None
        try:
            lb = await _leaderboard_positions_text()
        except Exception as exc:  # noqa: BLE001
            logger.warning("leaderboard for evening report failed: %s", exc)
        try:
            report = await daily_log.build_daily_report(
                api, config.HIVE_USERNAME, lb
            )
            await _send_daily_report(report)
            daily_log.archive_day()
        except Exception as exc:  # noqa: BLE001
            logger.exception("evening report failed")
            await _notify(f"Evening report failed: {exc}")


@dp.message(Command("daily"))
async def cmd_daily(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        text = await _run_daily_tasks_text()
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily failed")
        await _reply(message, f"Failed to run daily tasks: {exc}")
        return
    await _safe_reply(message, text)


@dp.message(Command("report"))
async def cmd_report(message: Message) -> None:
    """Build and send the daily activity report on demand."""
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        lb = None
        try:
            lb = await _leaderboard_positions_text()
        except Exception as exc:  # noqa: BLE001
            logger.warning("leaderboard for report failed: %s", exc)
        report = await daily_log.build_daily_report(
            api, config.HIVE_USERNAME, lb
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("report failed")
        await _reply(message, f"Failed to build report: {exc}")
        return
    await _safe_reply(message, report)


_GOODS_STATE_KEY = "goods_claim"


async def _run_goods_claim() -> None:
    """Claim goods now and persist the next planned claim time.

    Called by the scheduler when the stored planned time is reached. After
    claiming, computes the next ready time from the new cycle and writes it
    back to state.json so the loop keeps the cadence alive.
    """
    p = await api.goods_preview(config.HIVE_USERNAME)
    if not p.get("playerClaimReady"):
        remaining = int(
            p.get("remainingGoodsClaimSeconds")
            or p.get("remaining_goods_claim_seconds")
            or 0
        )
        if remaining > 0:
            scheduler.set_planned(
                _GOODS_STATE_KEY,
                datetime.now().astimezone()
                + timedelta(seconds=remaining + intervals.GOODS_CLAIM_BUFFER_SECONDS),
            )
            return
        scheduler.clear_planned(_GOODS_STATE_KEY)
        return
    d = await api.goods_claim(config.HIVE_USERNAME)
    claim_text = "Goods auto-claim:\n" + format_goods_claim(d)
    if config.AUTO_REDEMPTION:
        try:
            claim_text += "\n\n" + await _auto_redeem_goods()
        except Exception as exc:  # noqa: BLE001
            claim_text += f"\n\nGoods redemption failed: {exc}"
            logger.exception("auto redeem failed")
    await _notify(claim_text)
    fresh = await api.goods_preview(config.HIVE_USERNAME)
    remaining = int(
        fresh.get("remainingGoodsClaimSeconds")
        or fresh.get("remaining_goods_claim_seconds")
        or 0
    )
    if remaining > 0:
        scheduler.set_planned(
            _GOODS_STATE_KEY,
            datetime.now().astimezone()
            + timedelta(seconds=remaining + intervals.GOODS_CLAIM_BUFFER_SECONDS),
        )
    else:
        scheduler.clear_planned(_GOODS_STATE_KEY)


async def _schedule_from_state(now: datetime) -> None:
    """Schedule a wake-up task for the stored planned time, if due soon.

    Reads the persisted planned time from state.json. If none is stored, the
    plan is computed from the live preview and saved; the next loop iteration
    then picks it up. If one is stored, spawn a precise asyncio task to run
    the action at that moment instead of polling.
    """
    global _delayed_claim_task
    if _delayed_claim_task is not None and not _delayed_claim_task.done():
        return
    planned = scheduler.get_planned(_GOODS_STATE_KEY)
    if planned is None:
        try:
            p = await api.goods_preview(config.HIVE_USERNAME)
        except Exception as exc:  # noqa: BLE001
            logger.warning("goods schedule preview failed: %s", exc)
            return
        if p.get("playerClaimReady"):
            scheduler.set_planned(
                _GOODS_STATE_KEY, now + timedelta(seconds=1)
            )
        else:
            remaining = int(
                p.get("remainingGoodsClaimSeconds")
                or p.get("remaining_goods_claim_seconds")
                or 0
            )
            if remaining > 0:
                scheduler.set_planned(
                    _GOODS_STATE_KEY,
                    now
                    + timedelta(
                        seconds=remaining + intervals.GOODS_CLAIM_BUFFER_SECONDS
                    ),
                )
            else:
                scheduler.clear_planned(_GOODS_STATE_KEY)
        return
    wait = (planned - now).total_seconds()
    if wait <= 0:
        _delayed_claim_task = asyncio.create_task(
            _goods_claim_and_requeue()
        )
    elif wait <= intervals.GOODS_SCHEDULE_MAX_AHEAD_SECONDS:
        _delayed_claim_task = asyncio.create_task(
            _goods_claim_and_requeue(wait=wait)
        )


async def _goods_claim_and_requeue(wait: float = 0.0) -> None:
    """Wait until planned time, claim, then persist the next planned time."""
    global _delayed_claim_task
    try:
        if wait > 0:
            await asyncio.sleep(wait)
        await _run_goods_claim()
    except Exception as exc:  # noqa: BLE001
        logger.exception("scheduled goods claim failed")
        await _notify(f"Goods auto-claim failed: {exc}")
    finally:
        _delayed_claim_task = None


async def _check_wheel_auto() -> None:
    """Spin the wheel whenever spins are available, throttled via state.json.

    The daily 02:00 run covers the wheel once, but a spin earned mid-day would
    otherwise sit unused until the next daily run. This runs on a throttled
    schedule from the scheduler loop.
    """
    last = scheduler.get_planned("wheel_last_check")
    now = datetime.now().astimezone()
    if last is not None:
        wait = (now - last).total_seconds()
        if wait < intervals.WHEEL_RECHECK_INTERVAL_SECONDS:
            return
    w = await api.activity_wheel(config.HIVE_USERNAME)
    spins = int(w.get("availableSpins") or 0)
    if spins <= 0:
        scheduler.set_planned(
            "wheel_last_check",
            now + timedelta(seconds=intervals.WHEEL_RECHECK_EMPTY_INTERVAL_SECONDS),
        )
        return
    scheduler.set_planned("wheel_last_check", now)
    await _plan_wheel_text()


async def _scheduler_loop() -> None:
    """Maintenance loop: read state.json, schedule planned actions.

    Runs cheaply (local file read + rare preview). The actual goods claim is
    triggered at the persisted planned time by an asyncio task, so no constant
    API polling is required. The loop itself never dies: every iteration is
    wrapped so one failure only skips that step.
    """
    while True:
        try:
            now = datetime.now().astimezone()
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler clock failed: %s", exc)
            await asyncio.sleep(intervals.GOODS_SCHEDULER_REFRESH_SECONDS)
            continue
        try:
            await _schedule_from_state(now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler step failed: %s", exc)
        try:
            await _check_wheel_auto()
        except Exception as exc:  # noqa: BLE001
            logger.warning("wheel auto check failed: %s", exc)
        try:
            await _check_crate_auto()
        except Exception as exc:  # noqa: BLE001
            logger.warning("crate auto check failed: %s", exc)
        await asyncio.sleep(intervals.GOODS_SCHEDULER_REFRESH_SECONDS)


async def main() -> None:
    global _bot
    _bot = Bot(
        config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        scheduler = asyncio.create_task(_daily_tasks_loop())
        evening = asyncio.create_task(_evening_report_loop())
        goods_sched = asyncio.create_task(_scheduler_loop())
        await dp.start_polling(
            _bot, timeout=intervals.TG_POLLING_TIMEOUT_SECONDS
        )
    finally:
        scheduler.cancel()
        evening.cancel()
        goods_sched.cancel()
        await api.close()
        await _bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
