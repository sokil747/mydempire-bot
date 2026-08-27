import asyncio
import logging
from datetime import datetime, timezone

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

import config
import intervals

TIMEOUT = aiohttp.ClientTimeout(total=intervals.API_TIMEOUT_SECONDS)
_TOKEN_FILE = Path(__file__).resolve().parent / ".mde_token.json"

logger = logging.getLogger("mde_bot.api")


class MydEmpireAPIError(Exception):
    pass


class RateLimitedError(MydEmpireAPIError):
    pass


class MydEmpireClient:
    def __init__(self, base_url: str = config.MDE_API_BASE) -> None:
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._auth_lock = asyncio.Lock()
        self._load_token()

    def _load_token(self) -> None:
        try:
            if _TOKEN_FILE.exists():
                data = json.loads(_TOKEN_FILE.read_text())
                token = data.get("token")
                exp = data.get("expiresAt")
                if token and exp:
                    try:
                        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                        if exp_dt > datetime.now(timezone.utc) + timedelta(seconds=60):
                            self._token = token
                            self._token_expires_at = exp_dt
                            logger.info("Loaded cached MydEmpire token, expires %s", exp)
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Failed to load token file: %s", exc)

    def _save_token(self, token: str, expires_at_raw: str | None) -> None:
        try:
            _TOKEN_FILE.write_text(json.dumps({"token": token, "expiresAt": expires_at_raw}))
            try:
                _TOKEN_FILE.chmod(0o600)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to save token file: %s", exc)

    def _is_token_valid(self) -> bool:
        if not self._token or not self._token_expires_at:
            return False
        # consider token valid if it expires more than 60s from now
        now = datetime.now(timezone.utc)
        return self._token_expires_at > now + timedelta(seconds=60)

    async def _ensure_auth(self, force: bool = False) -> None:
        if not force and self._is_token_valid():
            return
        async with self._auth_lock:
            if not force and self._is_token_valid():
                return
            # need Hive posting key
            wif = config.HIVE_POSTING_KEY.strip()
            username = config.HIVE_USERNAME
            if not wif or not username:
                logger.warning("HIVE_POSTING_KEY or HIVE_USERNAME missing, cannot authenticate")
                return
            try:
                # 1. get challenge - use raw request without auth header to avoid recursion
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession(timeout=TIMEOUT)
                url = f"{self.base_url}/auth/challenge"
                # retry once on 429 with backoff
                for attempt in range(2):
                    async with self._session.request("POST", url, json={"username": username}) as resp:
                        if resp.status == 429:
                            if attempt == 0:
                                await asyncio.sleep(5)
                                continue
                            raise RateLimitedError(f"HTTP 429 for {url}")
                        if resp.status >= 400:
                            body = (await resp.text())[:500]
                            raise MydEmpireAPIError(f"HTTP {resp.status} for {url}: {body}")
                        data = await resp.json()
                    break
                if not data.get("success", True):
                    raise MydEmpireAPIError(data.get("error", "Unknown auth challenge error"))
                challenge = data.get("challenge")
                challenge_id = data.get("challengeId")
                if not challenge or not challenge_id:
                    raise MydEmpireAPIError("Invalid challenge response")
                # 2. sign challenge with posting key (hex encoding matches backend expectation)
                try:
                    from beemgraphenebase.ecdsasig import sign_message
                except ImportError as exc:
                    raise MydEmpireAPIError(f"beem not installed for signing: {exc}")
                signature = sign_message(challenge, wif).hex()
                # 3. verify
                url2 = f"{self.base_url}/auth/verify"
                async with self._session.request(
                    "POST", url2, json={"username": username, "challengeId": challenge_id, "signature": signature}
                ) as resp:
                    if resp.status == 429:
                        raise RateLimitedError(f"HTTP 429 for {url2}")
                    if resp.status >= 400:
                        body = (await resp.text())[:500]
                        raise MydEmpireAPIError(f"HTTP {resp.status} for {url2}: {body}")
                    vdata = await resp.json()
                if not vdata.get("success"):
                    raise MydEmpireAPIError(vdata.get("error", "Unknown auth verify error"))
                token = vdata.get("token")
                expires_at_raw = vdata.get("expiresAt")
                if not token:
                    raise MydEmpireAPIError("No token in verify response")
                self._token = token
                try:
                    self._token_expires_at = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00")) if expires_at_raw else None
                except Exception:
                    self._token_expires_at = None
                self._save_token(token, expires_at_raw)
                logger.info("MydEmpire session refreshed, expires %s", expires_at_raw)
            except RateLimitedError:
                raise
            except Exception as exc:
                logger.exception("auth refresh failed: %s", exc)
                # keep old token if any, but clear on auth error to force retry next time
                # don't clear token here, let caller handle 401
                raise MydEmpireAPIError(f"Auth failed: {exc}") from exc

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        # skip auth for auth endpoints themselves
        is_auth_path = path.startswith("/auth/")
        # ensure we have a token for non-auth paths if possible, but don't fail if no posting key
        if not is_auth_path and not self._is_token_valid():
            try:
                await self._ensure_auth()
            except Exception as exc:
                # log but continue without token - request may still succeed for public endpoints or fail with 401 which will trigger retry
                logger.warning("pre-request auth ensure failed: %s", exc)

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=TIMEOUT)
        url = f"{self.base_url}{path}"

        # inject Authorization if we have a token and not already set
        headers = kwargs.get("headers") or {}
        # copy to avoid mutating caller dict
        headers = dict(headers)
        if self._token and "Authorization" not in headers and not is_auth_path:
            headers["Authorization"] = f"Bearer {self._token}"
        kwargs["headers"] = headers

        async with self._session.request(method, url, **kwargs) as resp:
            if resp.status == 429:
                raise RateLimitedError(f"HTTP 429 rate limited for {url}")
            if resp.status == 401:
                body = (await resp.text())[:500]
                # session expired - try to refresh once and retry
                if not is_auth_path and "session" in body.lower():
                    logger.warning("401 session expired for %s, refreshing token", path)
                    try:
                        await self._ensure_auth(force=True)
                    except Exception as exc:
                        raise MydEmpireAPIError(f"HTTP 401 for {url}: {body}") from exc
                    # retry once with new token
                    headers["Authorization"] = f"Bearer {self._token}"
                    kwargs["headers"] = headers
                    async with self._session.request(method, url, **kwargs) as resp2:
                        if resp2.status == 429:
                            raise RateLimitedError(f"HTTP 429 rate limited for {url} (retry)")
                        if resp2.status >= 400:
                            body2 = (await resp2.text())[:500]
                            raise MydEmpireAPIError(f"HTTP {resp2.status} for {url}: {body2}")
                        data = await resp2.json()
                    if not data.get("success", True):
                        raise MydEmpireAPIError(data.get("error", "Unknown API error"))
                    return data
                raise MydEmpireAPIError(f"HTTP {resp.status} for {url}: {body}")
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

    async def goods_inventory(self, username: str) -> dict:
        return await self.get_json(
            f"/goods/{username}/inventory",
            headers={"x-mde-actor": username},
        )

    async def goods_burn_redemption(self, username: str, goods_ids: list[int]) -> dict:
        return await self._request(
            "POST",
            f"/goods-redemption/{username}/burn",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={"username": username, "goods_ids": goods_ids},
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

    async def factory_upgrade(self, username: str, factory_id: int) -> dict:
        return await self._request(
            "POST",
            "/factory/upgrade",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
            json={"username": username, "factory_id": factory_id},
        )

    async def dashboard(self, username: str) -> dict:
        return await self.get_json(f"/player/{username}/dashboard")

    async def rat_cleanup(self, username: str) -> dict:
        return await self._request(
            "POST",
            f"/player/{username}/rat-cleanup",
            headers={
                "Content-Type": "application/json",
                "x-mde-actor": username,
            },
        )

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

    async def goods_redemption_position(self, username: str) -> dict:
        return await self.get_json(f"/goods-redemption/{username}/position")

    async def goods_redemption_leaderboard(self) -> dict:
        return await self.get_json("/goods-redemption/leaderboard")

    async def emperor_leaderboard(self) -> dict:
        return await self.get_json("/leaderboard/emperors")

    async def season_leaderboard(self) -> dict:
        return await self.get_json("/season/active/leaderboard")

    async def active_season(self) -> dict:
        return await self.get_json("/season/active")

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
