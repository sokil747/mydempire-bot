import aiohttp

import config
import intervals

TIMEOUT = aiohttp.ClientTimeout(total=intervals.API_TIMEOUT_SECONDS)


class MydEmpireAPIError(Exception):
    pass


class RateLimitedError(MydEmpireAPIError):
    pass


class MydEmpireClient:
    def __init__(self, base_url: str = config.MDE_API_BASE) -> None:
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
        url = f"{self.base_url}{path}"
        async with self._session.request(method, url, **kwargs) as resp:
            if resp.status == 429:
                raise RateLimitedError(f"HTTP 429 rate limited for {url}")
            if resp.status >= 400:
                body = (await resp.text())[:300]
                raise MydEmpireAPIError(f"HTTP {resp.status} for {url}: {body}")
            data = await resp.json()
        if not data.get("success", True):
            raise MydEmpireAPIError(data.get("error", "Unknown API error"))
        return data

    async def get_json(self, path: str, **kwargs) -> dict:
        return await self._request("GET", path, **kwargs)

    async def post_json(self, path: str, payload: dict) -> dict:
        return await self._request("POST", path, json=payload)

    async def goods_preview(self, username: str) -> dict:
        return await self.get_json(
            f"/goods/{username}/preview",
            headers={"x-mde-actor": username},
        )

    async def goods_claim(self, username: str) -> dict:
        return await self._request(
            "POST",
            f"/goods/{username}/claim",
            headers={"x-mde-actor": username},
            json={"username": username},
        )

    async def claim_rewards(self, username: str) -> dict:
        return await self.get_json(
            f"/player/{username}/claim-rewards",
            headers={"x-mde-actor": username},
        )

    async def request_withdraw(self, username: str, amount: float) -> dict:
        return await self.get_json(
            f"/player/{username}/request-withdraw/{amount:.8f}",
            headers={"x-mde-actor": username},
        )

    async def crate_history(self, username: str) -> dict:
        return await self.get_json(
            f"/crate-history/{username}",
            headers={"x-mde-actor": username},
        )

    async def open_imperial_crate(self, username: str) -> dict:
        return await self._request(
            "POST",
            "/open-imperial-crate",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={"username": username},
        )

    async def factory_pay_maintenance(
        self, username: str, factory_id: int, days: int = 7
    ) -> dict:
        return await self._request(
            "POST",
            "/factory/pay-maintenance",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={"username": username, "factory_id": factory_id, "days": days},
        )

    async def dashboard(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/dashboard")

    async def global_stats(self) -> dict:
        return await self.get_json("/global-stats")

    async def global_health(self) -> dict:
        return await self.get_json("/global-health")

    async def reward_summary(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/reward-summary")

    async def empire_overview(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/empire-overview")

    async def empire_operations(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/empire-operations")

    async def start_operation(
        self, username: str, operation_type: str, budget: int
    ) -> dict:
        return await self._request(
            "POST",
            "/empire-operations/start",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={
                "username": username,
                "operation_type": operation_type,
                "budget": budget,
            },
        )

    async def collect_operation(
        self, username: str, operation_id: int
    ) -> dict:
        return await self._request(
            "POST",
            "/empire-operations/collect",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={"username": username, "operation_id": operation_id},
        )

    async def factory_fulfillment(self, username: str) -> dict:
        return await self.get_json(
            f"/player/{username}/factory-fulfillment",
            headers={"x-mde-actor": username},
        )

    async def factory_fulfillment_claim(self, username: str) -> dict:
        return await self._request(
            "POST",
            "/factory-fulfillment/claim",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={"username": username},
        )

    async def factory_fulfillment_start(
        self, username: str, fulfillment_type: str, industry: str
    ) -> dict:
        return await self._request(
            "POST",
            "/factory-fulfillment/start",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={
                "username": username,
                "fulfillmentType": fulfillment_type,
                "industry": industry,
            },
        )

    async def activity_wheel(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/activity-wheel")

    async def activity_wheel_spin(self, username: str) -> dict:
        return await self._request(
            "POST",
            f"/player/{username}/activity-wheel/spin",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={"username": username},
        )

    async def notifications(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/notifications")

    async def emp_history(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/emp-history")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
