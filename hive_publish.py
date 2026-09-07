"""Publish MydEmpire Daily Empire Report on Hive and claim the reward.

Mirrors the dashboard's publish flow:
  1. GET  /player/{user}/daily-empire-report      -> report (activities, marker)
  2. Comment on the latest @peak.snaps container signed with the posting key
  3. POST /player/{user}/daily-empire-report/claim -> +5 EMP +1 AP
"""

import asyncio
import json
import logging
import random
import re

import aiohttp

import config

logger = logging.getLogger("mde_bot.hive_publish")

HIVE_RPC = "https://api.hive.blog"


async def _hive_rpc(session: aiohttp.ClientSession, method: str, params) -> dict:
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    async with session.post(HIVE_RPC, json=payload) as resp:
        data = await resp.json()
    if data.get("error"):
        raise RuntimeError(f"Hive RPC error: {data['error'].get('message')}")
    return data.get("result")


async def _latest_snaps_container(session: aiohttp.ClientSession) -> tuple[str, str]:
    posts = await _hive_rpc(
        session, "condenser_api.get_discussions_by_blog",
        [{"tag": "peak.snaps", "limit": 20}],
    )
    authored = [p for p in (posts or []) if str(p.get("author", "")).lower() == "peak.snaps"]
    if not authored:
        raise RuntimeError("No @peak.snaps posts found.")
    container = next(
        (p for p in authored if re.search("snaps container", f"{p.get('title', '')} {p.get('body', '')}", re.I)),
        authored[0],
    )
    return container["author"], container["permlink"]


def _build_report_body(report: dict, image_url: str) -> str:
    activities = "\n".join(
        f"- {a.get('activity_type')}: {a.get('description')}"
        for a in (report.get("activities") or [])
    ) or "- No important activity recorded."
    marker = report.get("marker", "")
    return (
        "## MydEmpire Daily Empire Report\n\n"
        f"**UTC date:** {report.get('report_date')}\n\n"
        f"{activities}\n\n"
        f"![Daily Empire Report]({image_url})\n\n"
        "🎮 Play MydEmpire: https://www.mydempire.com\n\n"
        "#mydempire #hivegame #web3game #hive\n\n"
        f"{marker}"
    )


def _publish_comment_blocking(
    wif: str, author: str, parent_author: str, parent_permlink: str,
    permlink: str, title: str, body: str, json_metadata: str,
) -> bool:
    """Sign and broadcast a comment op. Returns True on success."""
    from beem import Hive
    from beembase.operations import Comment

    h = Hive(node=HIVE_RPC, nobroadcast=False)
    tx = h.txbuffer
    op = Comment(
        **{
            "parent_author": parent_author,
            "parent_permlink": parent_permlink,
            "author": author,
            "permlink": permlink,
            "title": title,
            "body": body,
            "json_metadata": json_metadata,
        }
    )
    tx.appendOps([op])
    tx.appendWif(wif)
    tx.constructTx()
    tx.sign()
    tx.broadcast()
    return True


def _publish_comment_blocking(
    wif: str, author: str, parent_author: str, parent_permlink: str,
    permlink: str, title: str, body: str, json_metadata: str,
) -> bool:
    """Sign and broadcast a comment op. Returns True on success."""
    from beem import Hive
    from beembase.operations import Comment

    h = Hive(node=HIVE_RPC, nobroadcast=False)
    tx = h.txbuffer
    op = Comment(
        **{
            "parent_author": parent_author,
            "parent_permlink": parent_permlink,
            "author": author,
            "permlink": permlink,
            "title": title,
            "body": body,
            "json_metadata": json_metadata,
        }
    )
    tx.appendOps([op])
    tx.appendWif(wif)
    tx.constructTx()
    tx.sign()
    tx.broadcast()
    return True


async def _verify_comment(author: str, permlink: str, marker: str) -> bool:
    async with aiohttp.ClientSession() as s:
        result = await _hive_rpc(
            s, "database_api.find_comments", {"comments": [[author, permlink]]}
        )
        comments = (result or {}).get("comments") or []
        for c in comments:
            if (
                str(c.get("author", "")).lower() == author.lower()
                and str(c.get("parent_author", "")).lower() == "peak.snaps"
                and marker in str(c.get("body", ""))
            ):
                return True
    return False


async def publish_daily_report(api, username: str) -> str:
    """Publish yesterday's Daily Empire Report on Hive and claim rewards.

    Returns a human-readable result string.
    """
    d = await api.daily_empire_report(username)
    report = d.get("report") or {}
    if not report:
        return "Daily Empire Report: nothing to publish."
    if report.get("claim") and report["claim"].get("hive_permlink"):
        link = report["claim"]["hive_permlink"]
        return (
            f"Daily Empire Report already published: "
            f"https://peakd.com/hive-124838/@{username}/{link}"
        )

    report_date = report.get("report_date")
    marker = report.get("marker", "")
    wif = config.HIVE_POSTING_KEY.strip()
    if not wif:
        return "Daily Empire Report: HIVE_POSTING_KEY missing, cannot publish."

    image_url = report.get("image_url") or ""
    body = _build_report_body(report, image_url)
    title = f"Daily Empire Report — {report_date}"
    permlink = (
        f"daily-empire-report-{report_date}-"
        f"{random.randint(0x1000000, 0xFFFFFFF):07x}"
    )
    metadata = json.dumps({
        "app": "mydempire/1.0",
        "format": "markdown",
        "tags": ["mydempire", "dailyreport"],
    })

    # 1) find container (async)
    async with aiohttp.ClientSession() as session:
        parent_author, parent_permlink = await _latest_snaps_container(session)

    # 2) sign + broadcast (blocking beem call in a thread)
    await asyncio.to_thread(
        _publish_comment_blocking,
        wif, username, parent_author, parent_permlink,
        permlink, title, body, metadata,
    )

    # 3) verify on chain (poll a few times)
    verified = False
    for _ in range(8):
        if await _verify_comment(username, permlink, marker):
            verified = True
            break
        await asyncio.sleep(1.5)
    if not verified:
        return (
            "Daily Empire Report published but not yet visible on Hive. "
            f"Claim not attempted: https://peakd.com/@{username}/{permlink}"
        )

    # 4) claim the reward
    claim = await api.daily_empire_report_claim(
        username, report_date, username, permlink
    )
    emp = claim.get("amount") or 5
    ap = claim.get("apAmount") or 1
    duplicate = claim.get("duplicate")
    status = "already claimed" if duplicate else f"+{emp} EMP +{ap} AP credited"
    try:
        import daily_log

        daily_log.log_action("Daily Empire Report published on Hive", detail=status)
    except Exception:
        pass
    return (
        f"Daily Empire Report {report_date} published on Hive ({status}): "
        f"https://peakd.com/hive-124838/@{username}/{permlink}"
    )