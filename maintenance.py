from datetime import datetime, timezone

REQUIRED_ACTIVE_DAYS = {"B1": 30, "B2": 60, "B3": 120, "B4": 240}
NEXT_TIER = {"B1": "B2", "B2": "B3", "B3": "B4", "B4": "B5"}
UPGRADE_COST_EMP = {"B1": 50, "B2": 100, "B3": 200, "B4": 400}
TIER_ORDER = {"B1": 0, "B2": 1, "B3": 2, "B4": 3}
LAND_MAX_LEVEL = {"L1": 3, "L2": 4, "L3": 5}


def _tier_short(blueprint_tier: str | None) -> str | None:
    """Return the short tier (e.g. TEXTILE_B2 -> B2) or None."""
    tier = str(blueprint_tier or "").upper()
    for candidate in ("B1", "B2", "B3", "B4", "B5"):
        if tier.endswith("_" + candidate):
            return candidate
    return None


def upgrade_ready(overview: dict) -> list[dict]:
    """Find factories that can be upgraded right now.

    Mirrors the player dashboard logic:
      status == ACTIVE, not max tier (B5), land level cap not reached,
      active_days >= required days, and the land does not already hold a B5
      (when the next tier would be B5).
    Returns a list of dicts: factory_id, factory_name, tier, next_tier, cost.
    """
    ready = []
    for land in overview.get("lands") or []:
        land_tier = str(land.get("land_tier") or land.get("tier") or "L1").upper()
        max_level = LAND_MAX_LEVEL.get(land_tier, 1)
        factories = land.get("factories") or []
        has_b5 = any(
            _tier_short(f.get("blueprint_tier")) == "B5"
            for f in factories
        )
        for f in factories:
            status = str(f.get("status") or "").upper()
            if status != "ACTIVE":
                continue
            tier = _tier_short(f.get("blueprint_tier"))
            if not tier or tier == "B5":
                continue
            level = int(tier[1])
            if level >= max_level:
                continue
            next_tier = NEXT_TIER[tier]
            required = REQUIRED_ACTIVE_DAYS[tier]
            active_days = float(f.get("active_days") or 0)
            if active_days < required:
                continue
            if next_tier == "B5" and has_b5:
                continue
            ready.append(
                {
                    "factory_id": f.get("id"),
                    "factory_name": f.get("factory_name") or f"Factory #{f.get('id')}",
                    "tier": tier,
                    "next_tier": next_tier,
                    "cost": UPGRADE_COST_EMP[tier],
                }
            )
    # Upgrade from the lowest level first: B1 -> B4.
    ready.sort(key=lambda f: TIER_ORDER.get(f["tier"], 99))
    return ready


def collect_factories(overview: dict) -> list[dict]:
    """Flatten factories from empire-overview and compute days_left."""
    now = datetime.now(timezone.utc)
    factories = []
    for land in overview.get("lands") or []:
        for f in land.get("factories") or []:
            try:
                ends = datetime.fromisoformat(
                    (f.get("maintenance_ends_at") or "").replace("Z", "+00:00")
                )
                days_left = (ends - now).total_seconds() / 86400
            except (KeyError, ValueError, TypeError, AttributeError):
                days_left = float("inf")
            factories.append(
                {
                    "id": f.get("id"),
                    "factory_name": f.get("factory_name", "?"),
                    "blueprint_tier": f.get("blueprint_tier", "?"),
                    "days_left": round(days_left, 2),
                    "status": f.get("status", "?"),
                }
            )
    return factories
