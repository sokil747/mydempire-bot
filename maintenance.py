from datetime import datetime, timezone


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
