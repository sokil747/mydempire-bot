import asyncio
import html
import logging
import random
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

import config
import intervals
from formatters import (
    _int,
    _num,
    format_crate_open,
    format_crate_plan,
    format_global_stats,
    format_goods_claim,
    format_goods_claim_plan,
    format_goods_preview,
    format_lands,
    format_maintenance,
    format_maintenance_paid,
    format_operations,
    format_reward_claim,
    format_rewards,
    format_status,
    format_wheel,
)
from maintenance import collect_factories
from mde_api import MydEmpireClient, MydEmpireAPIError, RateLimitedError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mde_bot")

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
        "/ops - empire operations\n"
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
    """Check factory maintenance and auto-pay due factories if balance positive.

    Returns the formatted report as a string.
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

    if not due:
        lines.append("")
        lines.append("No factories due for maintenance. All good.")
        return "\n".join(lines)

    if balance <= 0:
        lines.append("")
        lines.append(
            "Maintenance needed but EMP balance is not positive. "
            "Nothing paid."
        )
        return "\n".join(lines)

    lines.append("")
    lines.append(f"Balance positive, paying {len(due)} factory/factories...")
    result = await _pay_maintenance(factories, 2.0)
    pay_text = _format_pay_result(result, "=== Maintenance Auto-Pay ===")
    return "\n".join(lines + [pay_text])


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
        logger.info("[daily] %s", text[:500])
        return
    try:
        await _bot.send_message(
            chat_id,
            f"<pre>{html.escape(text[:4000])}</pre>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("notify failed: %s", exc)


async def _plan_goods_claim_text() -> str:
    """Check goods claim status; claim if ready, else plan a delayed claim.

    Returns a formatted plan/result string.
    """
    global _delayed_claim_task
    p = await api.goods_preview(config.HIVE_USERNAME)
    if p.get("playerClaimReady"):
        d = await api.goods_claim(config.HIVE_USERNAME)
        return "=== Goods Claim ===\n" + format_goods_claim(d)

    remaining = int(
        p.get("remainingGoodsClaimSeconds")
        or p.get("remaining_goods_claim_seconds")
        or 0
    )
    if remaining > 0:
        wait = remaining + intervals.GOODS_CLAIM_BUFFER_SECONDS
        scheduled_for = (datetime.now() + timedelta(seconds=wait)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if _delayed_claim_task is None or _delayed_claim_task.done():
            _delayed_claim_task = asyncio.create_task(
                _delayed_goods_claim(wait)
            )
        return format_goods_claim_plan(
            {
                "playerClaimReady": False,
                "remainingGoodsClaimSeconds": remaining,
                "claimed": False,
                "scheduled_for": scheduled_for,
                "status": (
                    f"Auto-claim scheduled in {int(wait)}s "
                    "(cooldown + 1s buffer)"
                ),
            }
        )
    return format_goods_claim_plan(
        {
            "playerClaimReady": False,
            "remainingGoodsClaimSeconds": 0,
            "claimed": False,
            "status": "No active cycle / not ready.",
        }
    )


async def _delayed_goods_claim(wait: float) -> None:
    """Sleep until cooldown expires, then claim goods and notify."""
    global _delayed_claim_task
    try:
        await asyncio.sleep(wait)
        p = await api.goods_preview(config.HIVE_USERNAME)
        for _ in range(intervals.GOODS_CLAIM_MAX_CHECK_ATTEMPTS):
            if p.get("playerClaimReady"):
                break
            await asyncio.sleep(intervals.GOODS_CLAIM_RECHECK_DELAY_SECONDS)
            p = await api.goods_preview(config.HIVE_USERNAME)
        if p.get("playerClaimReady"):
            d = await api.goods_claim(config.HIVE_USERNAME)
            await _notify("Goods auto-claim done:\n" + format_goods_claim(d))
        else:
            await _notify(
                "Goods still not ready after cooldown expired. Skipped."
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("delayed goods claim failed")
        await _notify(f"Goods auto-claim failed: {exc}")
    finally:
        _delayed_claim_task = None


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def _crate_status() -> dict:
    """Determine current crate availability from history + cooldown rules.

    Only the first free crate of the day is considered openable.
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

    d = await api.dashboard(config.HIVE_USERNAME)
    eligible = float(d.get("globalSharePercent") or 0) >= 1.5

    cooldown_until = None
    if last_opened is not None:
        cooldown_until = last_opened + timedelta(
            seconds=intervals.CRATE_COOLDOWN_SECONDS
        )

    can_open = opened_today == 0 and eligible
    cooldown_remaining = None
    if can_open and cooldown_until is not None and now < cooldown_until:
        can_open = False
        cooldown_remaining = str(cooldown_until - now)

    return {
        "can_open": can_open,
        "opened_today": opened_today,
        "eligible": eligible,
        "last_opened": last_opened,
        "cooldown_until": cooldown_until,
        "cooldown_remaining": cooldown_remaining,
    }


async def _plan_crate_text() -> str:
    """Open the free daily crate if available, else report the plan."""
    status = await _crate_status()
    if status["can_open"]:
        d = await api.open_imperial_crate(config.HIVE_USERNAME)
        return "=== Imperial Supply Crate ===\n" + format_crate_open(d)
    if status["opened_today"] >= 1:
        reason = "Already opened today (only 1 free crate per day)."
    elif not status["eligible"]:
        reason = "Not eligible for free crate (need globalShare >= 1.5%)."
    elif status["cooldown_remaining"]:
        reason = f"On cooldown: {status['cooldown_remaining']}"
    else:
        reason = "Not available."
    return format_crate_plan(
        {
            "can_open": False,
            "opened_today": status["opened_today"],
            "cooldown_remaining": status["cooldown_remaining"] or "n/a",
            "status": reason,
        }
    )


async def _run_daily_tasks_text() -> str:
    """Run the daily routine: claim HIVE, check lands, goods, crate."""
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
    return "=== Daily Tasks ===\n\n" + "\n\n".join(parts)


async def _daily_scheduler_loop() -> None:
    """Run the daily tasks at the configured time each day (default 02:00)."""
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
            text = await _run_daily_tasks_text()
            await _notify(text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("daily tasks run failed")
            await _notify(f"Daily tasks failed: {exc}")


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


async def main() -> None:
    global _bot
    _bot = Bot(
        config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        scheduler = asyncio.create_task(_daily_scheduler_loop())
        await dp.start_polling(
            _bot, timeout=intervals.TG_POLLING_TIMEOUT_SECONDS
        )
    finally:
        scheduler.cancel()
        await api.close()
        await _bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
