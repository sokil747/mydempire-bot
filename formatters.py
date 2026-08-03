from typing import Any


def _num(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _int(value: Any) -> str:
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return "0"


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _pct_raw(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _or_na(value: Any) -> str:
    if value is None or str(value).strip() in ("", "null", "None", "0"):
        return "n/a"
    return str(value)


def format_status(username: str, d: dict) -> str:
    support = d.get("industrialSupport", {})
    lines = [
        f"=== MydEmpire Status: @{username} ===",
        "",
        "-- EMP --",
        f"Balance:           {_num(d.get('empBalance'))}",
        f"EP/day (current):  {_num(d.get('currentEpDay'))}",
        f"Lifetime EP:       {_num(d.get('lifetimeEP'))}",
        f"Claimable EP:      {_num(d.get('totalClaimableEP'))}",
        f"Base/Boosted EP:   {_num(d.get('totalBaseEP'))} / {_num(d.get('totalBoostedEP'))}",
        f"Global EP share:   {_pct_raw(d.get('globalSharePercent'))}",
        "",
        "-- Factories --",
        f"Total: {_int(d.get('totalFactories'))}  "
        f"Active: {_int(d.get('activeFactories'))}  "
        f"Building: {_int(d.get('buildingFactories'))}  "
        f"Inactive: {_int(d.get('inactiveFactories'))}",
        "",
        "-- Relics --",
        f"Count: {_int(d.get('relicCount'))}  "
        f"Active boost: {_pct(d.get('activeRelicBoost'))}",
        "",
        "-- SMP --",
        f"Balance:         {_num(d.get('smpBalance'))}",
        f"Expected daily:  {_num(d.get('expectedSmpMin'))} - {_num(d.get('expectedSmpMax'))}",
        "",
        "-- Industrial Support --",
        f"Today's pool:        {_num(support.get('todayPool'))}",
        f"Qualified players:   {_int(support.get('qualifiedPlayers'))}",
        f"Total qualified EP:  {_num(support.get('totalQualifiedEP'))}",
        f"Your EP/day:         {_num(support.get('yourEpDay'))}",
        f"Equal/EP share:      {_num(support.get('equalShare'))} / {_num(support.get('epShare'))}",
        f"Your reward:         {_num(support.get('yourReward'))}",
        f"Next distribution:   {support.get('nextDistribution', 'n/a')}",
        "",
        "-- Warehouse --",
        f"Condition: {d.get('warehouseCondition', 'n/a')}",
        f"Rat activity: {_num(d.get('ratActivity'))}",
        f"Clutter score: {_num(d.get('clutterScore'))}",
        f"Cleanup cost: {_num(d.get('ratCleanupCost'))}",
        f"Last cleanup: {_or_na(d.get('lastCleanupAt'))}",
        "",
        f"Writs: {_int(d.get('writCount'))}  "
        f"Imperial fragments: {_int(d.get('imperialFragments'))}",
    ]
    return "\n".join(lines)


def format_global_stats(g: dict, h: dict) -> str:
    ep = h.get("ep", {})
    treasury = h.get("treasury", {})
    lines = [
        "=== Global ===",
        f"Packs sold: {_int(g.get('totalPacksSold'))}  "
        f"Remaining: {_int(g.get('remainingGenesisPacks'))}",
        f"Factories: {_int(g.get('totalFactories'))}  "
        f"Active: {_int(g.get('activeFactories'))}",
        f"Lands: {_int(g.get('totalLands'))}  "
        f"Blueprints: {_int(g.get('totalBlueprints'))}",
        f"Total EMP supply: {_num(g.get('totalEmpSupply'))}",
        f"Treasury balance: {_num(treasury.get('balance'))}",
        f"Reward pool: {_num(treasury.get('rewardPool'))}",
        f"Treasury health: {treasury.get('healthLabel', 'n/a')}",
        f"Emission rate: {_or_na(treasury.get('emissionRate'))}",
        f"Live global EP: {_int(ep.get('liveGlobalEP'))}",
    ]
    return "\n".join(lines)


def format_rewards(r: dict) -> str:
    lines = [
        "=== Rewards ===",
        f"Claimable: {_num(r.get('claimable_amount'))}  "
        f"Entries: {_int(r.get('claimable_entries'))}",
        f"Wallet balance: {_num(r.get('wallet_balance'))}",
        f"Total claimed: {_num(r.get('total_claimed'))}",
        f"Total withdrawn: {_num(r.get('total_withdrawn'))}",
        f"Pending withdraw: {_num(r.get('pending_withdraw_amount'))}",
        f"Min withdrawal: {_num(r.get('minimum_withdrawal'))}",
        f"Last completed: {_num(r.get('last_completed_withdraw_amount'))}",
    ]
    return "\n".join(lines)


def format_wheel(w: dict) -> str:
    lines = [
        "=== Activity Wheel ===",
        f"AP: {_int(w.get('currentAP'))}  "
        f"Available spins: {_int(w.get('availableSpins'))}",
        f"Spin cost: {_int(w.get('spinCostAP'))} AP",
        f"Total spins: {_int(w.get('totalSpins'))}",
        f"Total AP earned: {_int(w.get('totalAPEarned'))}",
        f"Total AP spent: {_int(w.get('totalAPSpent'))}",
    ]
    rewards = w.get("rewards") or []
    if rewards:
        lines.append("")
        lines.append("Reward table:")
        for r in rewards[:6]:
            lines.append(
                f"  {r.get('reward_type')} x{r.get('reward_amount')} "
                f"- {_pct_raw(r.get('chance'))}"
            )
    return "\n".join(lines)


def format_maintenance(
    username: str, factories: list, threshold_days: float = 2.0
) -> str:
    low = [f for f in factories if f["days_left"] <= threshold_days]
    low.sort(key=lambda f: f["days_left"])
    lines = [
        f"=== Maintenance Check: @{username} ===",
        f"Factories with <= {_num(threshold_days)} days left: {len(low)} / {len(factories)}",
    ]
    if not low:
        lines.append("")
        lines.append("All factories have sufficient maintenance. Nothing to do.")
        return "\n".join(lines)
    lines.append("")
    for f in low:
        lines.append(
            f"#{f['id']} {f.get('factory_name', '?')} "
            f"- {_num(f['days_left'])}d ({f.get('blueprint_tier', '?')})"
        )
    return "\n".join(lines)


def format_maintenance_paid(r: dict) -> str:
    lines = [
        "=== Maintenance Paid ===",
        f"Factory: #{r.get('factory_id')}",
        f"Days added: {_int(r.get('days_added'))}",
        f"EMP spent: {_num(r.get('emp_spent'))}",
        f"Maintenance until: {r.get('maintenance_until', 'n/a')}",
    ]
    ap = r.get("activity_points") or {}
    if ap:
        lines.append(f"Activity points: +{_int(ap.get('points'))}")
    return "\n".join(lines)


def format_lands(username: str, d: dict) -> str:
    lands = d.get("lands") or []
    totals = d.get("totals") or {}

    l1 = l2 = l3 = 0
    total_slots = 0
    used_slots = 0
    factories = []
    for land in lands:
        tier = str(land.get("land_tier") or land.get("tier") or "").upper()
        if tier == "L1":
            l1 += 1
            total_slots += 1
        elif tier == "L2":
            l2 += 1
            total_slots += 2
        elif tier == "L3":
            l3 += 1
            total_slots += 3
        land_factories = land.get("factories") or []
        used_slots += len(land_factories)
        factories.extend(land_factories)

    active = sum(
        1
        for f in factories
        if str(f.get("status", "")).upper() in ("ACTIVE", "BUILDING", "UPGRADING")
    )
    inactive = len(factories) - active

    lines = [
        f"=== Lands Summary: @{username} ===",
        f"Lands owned: {_int(len(lands))}",
        f"  L1: {_int(l1)} | L2: {_int(l2)} | L3: {_int(l3)}",
        f"Slots: {_int(total_slots)} | Used: {_int(used_slots)} | Empty: {_int(max(total_slots - used_slots, 0))}",
        f"Factories: {_int(len(factories))} | Active: {_int(active)} | Inactive: {_int(inactive)}",
        f"Total EP/day: {_num(totals.get('totalBoostedEP', totals.get('totalBaseEP')))}",
        f"Relics: {_int(totals.get('relicCount'))}",
    ]

    tier_ep = {}
    for land in lands:
        tier = str(land.get("land_tier") or land.get("tier") or "").upper()
        land_ep = 0
        for f in land.get("factories") or []:
            if str(f.get("status", "")).upper() == "ACTIVE":
                land_ep += float(f.get("factory_ep") or f.get("factoryEP") or 0)
        tier_ep[tier] = tier_ep.get(tier, 0) + land_ep

    if tier_ep:
        lines.append("")
        lines.append("EP/day by land tier:")
        for tier in ("L1", "L2", "L3"):
            if tier in tier_ep:
                lines.append(f"  {tier}: {_num(tier_ep[tier])}")

    return "\n".join(lines)


def format_goods_claim(d: dict) -> str:
    goods = d.get("goods") or []
    lines = [
        "=== Goods Claimed ===",
        f"Factories processed: {_int(d.get('factories_processed', d.get('factoriesProcessed')))}",
        f"Goods received: {_int(d.get('goods_received', d.get('goodsReceived')) or len(goods))}",
        f"Total product value: {_num(d.get('total_product_value', d.get('totalProductValue')))} PV",
    ]
    by_level = d.get("byLevel") or d.get("by_level") or {}
    by_quality = d.get("byQuality") or d.get("by_quality") or {}
    if by_level:
        lines.append("By level: " + ", ".join(f"{k}: {v}" for k, v in by_level.items()))
    if by_quality:
        lines.append(
            "By quality: " + ", ".join(f"{k}: {v}" for k, v in by_quality.items())
        )
    if goods:
        lines.append("")
        lines.append("Rewards:")
        for g in goods[:10]:
            name = g.get("product_name", "?")
            qty = g.get("quantity", 1)
            pv = g.get("final_value", g.get("total_value", 0))
            lines.append(f"  {name} x{qty} - {_num(pv)} PV")
        if len(goods) > 10:
            lines.append(f"  ... and {len(goods) - 10} more")
    return "\n".join(lines)


def format_goods_preview(d: dict) -> str:
    lines = [
        "=== Goods Preview ===",
        f"Ready to claim: {'YES' if d.get('playerClaimReady') else 'NO'}",
        f"Ready factories: {_int(d.get('readyFactoryCount'))} / {_int(d.get('activeFactoryCount'))}",
        f"EP/day: {_num(d.get('epPerDay'))}",
        f"Capacity: {d.get('capacityLabel', 'n/a')}",
    ]
    remaining = d.get("remainingGoodsClaimSeconds")
    if remaining is not None:
        from datetime import timedelta

        lines.append(f"Next claim in: {timedelta(seconds=int(remaining))}")
    return "\n".join(lines)


def format_operations(op: dict) -> str:
    lines = [
        "=== Empire Operations ===",
        f"Current EP: {_num(op.get('currentEP'))}",
        f"Industrial authority: {_num(op.get('industrialAuthority'))}",
        f"EMP balance: {_num(op.get('empBalance'))}",
    ]
    active = op.get("activeOperation")
    if active:
        lines.append(f"Active operation: {active.get('operation_type', 'n/a')}")
        lines.append(
            f"  Progress: {_or_na(active.get('narrative', active.get('status', 'n/a')))}"
        )
    else:
        lines.append("Active operation: none")
    history = op.get("history") or []
    if history:
        lines.append(f"History entries: {len(history)}")
    return "\n".join(lines)
