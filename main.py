import asyncio
import html
import logging
import random

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
    format_global_stats,
    format_goods_claim,
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


@dp.message(Command("claimhive"))
async def cmd_claimhive(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
        r = await api.reward_summary(config.HIVE_USERNAME)
        amount = float(r.get("claimable_amount") or 0)
        if amount <= 0:
            await _reply(message, "No HIVE available to claim right now.")
            return
        d = await api.claim_rewards(config.HIVE_USERNAME)
        text = format_reward_claim(d)
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


@dp.message(Command("check_lands"))
async def cmd_check_lands(message: Message) -> None:
    try:
        await message.bot.send_chat_action(
            chat_id=message.chat.id, action=ChatAction.TYPING
        )
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
            await _safe_reply(message, "\n".join(lines))
            return

        if balance <= 0:
            lines.append("")
            lines.append(
                "Maintenance needed but EMP balance is not positive. "
                "Nothing paid."
            )
            await _safe_reply(message, "\n".join(lines))
            return

        lines.append("")
        lines.append(f"Balance positive, paying {len(due)} factory/factories...")
        await _safe_reply(message, "\n".join(lines))

        result = await _pay_maintenance(factories, 2.0)
        text = _format_pay_result(result, "=== Maintenance Auto-Pay ===")
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


async def main() -> None:
    bot = Bot(
        config.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        await dp.start_polling(
            bot, timeout=intervals.TG_POLLING_TIMEOUT_SECONDS
        )
    finally:
        await api.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
