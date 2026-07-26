from __future__ import annotations

import asyncio
import secrets
import time
from typing import cast
from urllib.parse import urlencode

import httpx
import structlog

from app.broker.base import (
    BrokerAuthenticationError,
    BrokerUnavailableError,
    BrokerValidationError,
)
from app.config import Settings
from app.domain.entities import BrokerOrder, BrokerOrderResult, OrderSide, OrderType

logger = structlog.get_logger()


class SaxoTokenManager:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client
        self._access_token = (
            settings.saxo_access_token.get_secret_value() if settings.saxo_access_token else None
        )
        self._refresh_token = (
            settings.saxo_refresh_token.get_secret_value() if settings.saxo_refresh_token else None
        )
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def authorization_url(self, state: str) -> str:
        if not self.settings.saxo_app_key or not self.settings.saxo_redirect_uri:
            raise BrokerAuthenticationError("Saxo OAuth settings are incomplete")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.settings.saxo_app_key.get_secret_value(),
                "state": state,
                "redirect_uri": self.settings.saxo_redirect_uri,
            }
        )
        return f"{self.auth_base_url}/authorize?{query}"

    async def exchange_code(self, code: str) -> None:
        await self._exchange(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.saxo_redirect_uri or "",
            }
        )

    async def access_token(self) -> str:
        if self._access_token and (self._expires_at == 0 or self._expires_at > time.time() + 60):
            return self._access_token
        async with self._lock:
            if self._access_token and (
                self._expires_at == 0 or self._expires_at > time.time() + 60
            ):
                return self._access_token
            if not self._refresh_token:
                raise BrokerAuthenticationError("Saxo access token is unavailable")
            await self._exchange(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "redirect_uri": self.settings.saxo_redirect_uri or "",
                }
            )
            if not self._access_token:
                raise BrokerAuthenticationError("Saxo did not return an access token")
            return self._access_token

    @property
    def auth_base_url(self) -> str:
        return (
            self.settings.saxo_live_auth_base_url
            if self.settings.trading_mode == "live"
            else self.settings.saxo_sim_auth_base_url
        )

    async def _exchange(self, data: dict[str, str]) -> None:
        if not self.settings.saxo_app_key or not self.settings.saxo_app_secret:
            raise BrokerAuthenticationError("Saxo client credentials are unavailable")
        response = await self.client.post(
            f"{self.auth_base_url}/token",
            data=data,
            auth=(
                self.settings.saxo_app_key.get_secret_value(),
                self.settings.saxo_app_secret.get_secret_value(),
            ),
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise BrokerAuthenticationError(
                f"Saxo token exchange failed with HTTP {response.status_code}"
            )
        payload = response.json()
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise BrokerAuthenticationError("Saxo token response is invalid")
        self._access_token = access_token
        refresh_token = payload.get("refresh_token")
        if isinstance(refresh_token, str) and refresh_token:
            self._refresh_token = refresh_token
        expires_in = payload.get("expires_in", 1_200)
        self._expires_at = time.time() + max(60, int(expires_in))


class SaxoBroker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "AI-Tader/0.1"},
        )
        self.tokens = SaxoTokenManager(settings, self.client)

    @property
    def api_base_url(self) -> str:
        return (
            self.settings.saxo_live_api_base_url
            if self.settings.trading_mode == "live"
            else self.settings.saxo_sim_api_base_url
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def health(self) -> bool:
        try:
            await self.account_info()
            return True
        except Exception:
            return False

    async def account_info(self) -> dict[str, object]:
        return await self._request("GET", "/port/v1/accounts/me")

    async def portfolio(self) -> dict[str, object]:
        positions, orders, balance = await asyncio.gather(
            self.positions(), self.orders(), self.balance()
        )
        return {"positions": positions, "orders": orders, "balance": balance}

    async def positions(self) -> dict[str, object]:
        return await self._request("GET", "/port/v1/positions/me")

    async def orders(self) -> dict[str, object]:
        return await self._request("GET", "/port/v1/orders/me")

    async def balance(self) -> dict[str, object]:
        return await self._request("GET", "/port/v1/balances/me")

    async def precheck(self, order: BrokerOrder) -> dict[str, object]:
        payload = self._order_payload(order)
        result = await self._request(
            "POST",
            "/trade/v2/orders/precheck",
            json=payload,
            request_id=order.idempotency_key,
            retry_safe=False,
        )
        if result.get("PreCheckResult") not in (None, "Ok"):
            raise BrokerValidationError(
                f"Saxo rejected order pre-check: {result.get('PreCheckResult')}"
            )
        if result.get("PreTradeDisclaimers"):
            raise BrokerValidationError("Saxo requires an interactive pre-trade disclaimer")
        return result

    async def place_bracket_order(self, order: BrokerOrder) -> BrokerOrderResult:
        await self.precheck(order)
        payload = self._order_payload(order)
        result = await self._request(
            "POST",
            "/trade/v2/orders",
            json=payload,
            request_id=order.idempotency_key,
            retry_safe=False,
        )
        error = result.get("ErrorInfo")
        if error:
            raise BrokerValidationError("Saxo returned an order error")
        order_id = result.get("OrderId")
        if order_id is None:
            raise BrokerUnavailableError(
                "Saxo order result is ambiguous; reconcile orders before retrying"
            )
        raw_orders = result.get("Orders", [])
        order_items = cast(list[object], raw_orders) if isinstance(raw_orders, list) else []
        related_ids = tuple(
            str(item["OrderId"])
            for item in order_items
            if isinstance(item, dict) and item.get("OrderId") is not None
        )
        return BrokerOrderResult(
            broker_order_id=str(order_id),
            related_order_ids=related_ids,
            status="accepted",
            raw=result,
        )

    def _order_payload(self, order: BrokerOrder) -> dict[str, object]:
        if order.side is not OrderSide.BUY:
            raise BrokerValidationError("version 1 only supports long entries")
        order_type = {
            OrderType.MARKET: "Market",
            OrderType.LIMIT: "Limit",
            OrderType.STOP: "Stop",
        }[order.order_type]
        payload: dict[str, object] = {
            "AccountKey": order.account_key,
            "Amount": order.quantity,
            "AssetType": order.asset_type,
            "BuySell": "Buy",
            "ExternalReference": order.idempotency_key[:50],
            "ManualOrder": False,
            "OrderDuration": {"DurationType": "DayOrder"},
            "OrderType": order_type,
            "Uic": order.uic,
            "Orders": [
                {
                    "BuySell": "Sell",
                    "OrderPrice": order.stop_loss,
                    "OrderType": "Stop",
                    "ManualOrder": False,
                    "OrderDuration": {"DurationType": "GoodTillCancel"},
                },
                {
                    "BuySell": "Sell",
                    "OrderPrice": order.take_profit,
                    "OrderType": "Limit",
                    "ManualOrder": False,
                    "OrderDuration": {"DurationType": "GoodTillCancel"},
                },
            ],
        }
        if order.order_type is not OrderType.MARKET:
            payload["OrderPrice"] = order.entry_price
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        request_id: str | None = None,
        retry_safe: bool = True,
    ) -> dict[str, object]:
        if method not in {"GET", "POST", "PATCH", "DELETE"}:
            raise ValueError("unsupported HTTP method")
        if not path.startswith("/") or ".." in path or "://" in path:
            raise ValueError("invalid Saxo path")
        token = await self.tokens.access_token()
        headers = {"Authorization": f"Bearer {token}"}
        if request_id:
            headers["X-Request-ID"] = request_id
        attempts = 3 if retry_safe and method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = await self.client.request(
                    method,
                    f"{self.api_base_url}{path}",
                    headers=headers,
                    json=json,
                )
            except httpx.HTTPError as exc:
                if attempt + 1 == attempts:
                    raise BrokerUnavailableError("Saxo network request failed") from exc
                await asyncio.sleep(0.25 * (2**attempt))
                continue
            if response.status_code == 401:
                raise BrokerAuthenticationError("Saxo rejected the access token")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 == attempts:
                    raise BrokerUnavailableError(
                        f"Saxo temporarily unavailable (HTTP {response.status_code})"
                    )
                retry_after = response.headers.get("retry-after", "1")
                try:
                    delay = min(float(retry_after), 5.0)
                except ValueError:
                    delay = 1.0
                await asyncio.sleep(delay)
                continue
            if response.status_code >= 400:
                logger.warning(
                    "saxo_request_rejected",
                    method=method,
                    path=path,
                    status=response.status_code,
                )
                raise BrokerValidationError(f"Saxo rejected request (HTTP {response.status_code})")
            payload = response.json()
            if not isinstance(payload, dict):
                raise BrokerUnavailableError("Saxo returned an invalid response")
            return payload
        raise BrokerUnavailableError("Saxo request failed")


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)
