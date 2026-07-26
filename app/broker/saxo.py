from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import urlencode

import httpx
import structlog
from cryptography.fernet import Fernet, InvalidToken

from app.broker.base import (
    BrokerAuthenticationError,
    BrokerUnavailableError,
    BrokerValidationError,
)
from app.config import Settings
from app.domain.entities import (
    BrokerOrder,
    BrokerOrderResult,
    Candle,
    MarketSnapshot,
    OrderSide,
    OrderType,
    PortfolioState,
)
from app.infrastructure.repositories import Repository

logger = structlog.get_logger()


class SaxoTokenManager:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        repository: Repository | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.repository = repository
        self.provider = f"saxo_{settings.trading_mode}"
        self._refresh_owner = str(uuid.uuid4())
        self._cipher = self._build_cipher()
        self._access_token = (
            settings.saxo_access_token.get_secret_value() if settings.saxo_access_token else None
        )
        self._refresh_token = (
            settings.saxo_refresh_token.get_secret_value() if settings.saxo_refresh_token else None
        )
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._load_persisted_tokens()

    def _build_cipher(self) -> Fernet | None:
        if not self.settings.token_encryption_key:
            return None
        try:
            return Fernet(self.settings.token_encryption_key.get_secret_value().encode())
        except (TypeError, ValueError) as exc:
            raise BrokerAuthenticationError("TOKEN_ENCRYPTION_KEY is invalid") from exc

    def _load_persisted_tokens(self) -> None:
        if self.repository is None or self._cipher is None:
            return
        credential = self.repository.get_broker_credential(self.provider)
        if credential is None:
            return
        try:
            encrypted_access = credential.get("encrypted_access_token")
            encrypted_refresh = credential.get("encrypted_refresh_token")
            if isinstance(encrypted_access, str) and encrypted_access:
                self._access_token = self._cipher.decrypt(encrypted_access.encode()).decode()
            if isinstance(encrypted_refresh, str) and encrypted_refresh:
                self._refresh_token = self._cipher.decrypt(encrypted_refresh.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise BrokerAuthenticationError("Stored Saxo credentials cannot be decrypted") from exc
        expires_at = credential.get("access_expires_at")
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            self._expires_at = expires_at.timestamp()

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
            self._load_persisted_tokens()
            if self._access_token and (
                self._expires_at == 0 or self._expires_at > time.time() + 60
            ):
                return self._access_token
            if not self._refresh_token:
                raise BrokerAuthenticationError("Saxo access token is unavailable")
            await self._refresh_with_distributed_lease()
            if not self._access_token:
                raise BrokerAuthenticationError("Saxo did not return an access token")
            return self._access_token

    async def _refresh_with_distributed_lease(self) -> None:
        if self.repository is None or self._cipher is None:
            await self._exchange(self._refresh_payload())
            return
        for _attempt in range(20):
            self._load_persisted_tokens()
            if self._access_token and self._expires_at > time.time() + 60:
                return
            lease_until = datetime.now(UTC) + timedelta(seconds=30)
            if self.repository.claim_broker_refresh_lease(
                self.provider, self._refresh_owner, lease_until
            ):
                try:
                    self._load_persisted_tokens()
                    if self._access_token and self._expires_at > time.time() + 60:
                        return
                    await self._exchange(self._refresh_payload())
                    return
                finally:
                    self.repository.release_broker_refresh_lease(self.provider, self._refresh_owner)
            await asyncio.sleep(0.5)
        raise BrokerUnavailableError("Timed out waiting for Saxo token refresh")

    def _refresh_payload(self) -> dict[str, str]:
        if not self._refresh_token:
            raise BrokerAuthenticationError("Saxo refresh token is unavailable")
        return {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "redirect_uri": self.settings.saxo_redirect_uri or "",
        }

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
        self._persist_tokens(payload)

    def _persist_tokens(self, payload: dict[str, object]) -> None:
        if self.repository is None or self._cipher is None:
            return
        refresh_expires_at = None
        refresh_expires_in = payload.get("refresh_token_expires_in")
        if isinstance(refresh_expires_in, (int, float, str)):
            refresh_expires_at = datetime.fromtimestamp(
                time.time() + max(60, int(refresh_expires_in)),
                tz=UTC,
            )
        self.repository.save_broker_credential(
            self.provider,
            encrypted_access_token=(
                self._cipher.encrypt(self._access_token.encode()).decode()
                if self._access_token
                else None
            ),
            encrypted_refresh_token=(
                self._cipher.encrypt(self._refresh_token.encode()).decode()
                if self._refresh_token
                else None
            ),
            access_expires_at=datetime.fromtimestamp(self._expires_at, tz=UTC),
            refresh_expires_at=refresh_expires_at,
        )


class SaxoBroker:
    def __init__(self, settings: Settings, repository: Repository | None = None) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": "AI-Tader/0.1"},
        )
        self.tokens = SaxoTokenManager(settings, self.client, repository)

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

    async def market_snapshot(self, *, uic: int, asset_type: str, symbol: str) -> MarketSnapshot:
        payload = await self._request(
            "GET",
            "/trade/v1/infoprices",
            params={"Uic": str(uic), "AssetType": asset_type},
        )
        raw_quote = payload.get("Quote", {})
        quote = cast(dict[str, object], raw_quote) if isinstance(raw_quote, dict) else {}
        bid = self._number(quote, "Bid")
        ask = self._number(quote, "Ask")
        delayed = int(self._number(payload, "DelayedByMinutes", default=0.0))
        return MarketSnapshot(
            symbol=symbol,
            bid=bid,
            ask=ask,
            as_of=datetime.now(UTC),
            data_quality_ok=bid > 0 and ask >= bid and delayed == 0,
            delayed_minutes=max(0, delayed),
        )

    async def historical_candles(
        self,
        *,
        uic: int,
        asset_type: str,
        horizon_minutes: int,
        count: int,
    ) -> list[Candle]:
        payload = await self._request(
            "GET",
            "/chart/v3/charts",
            params={
                "Uic": str(uic),
                "AssetType": asset_type,
                "Horizon": str(horizon_minutes),
                "Count": str(count),
                "Mode": "UpTo",
            },
        )
        raw_data = payload.get("Data", [])
        data = cast(list[object], raw_data) if isinstance(raw_data, list) else []
        candles: list[Candle] = []
        for raw in data:
            if not isinstance(raw, dict):
                continue
            item = cast(dict[str, object], raw)
            raw_time = item.get("Time")
            if not isinstance(raw_time, str):
                continue
            timestamp = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            open_price = self._chart_price(item, "Open")
            high_price = self._chart_price(item, "High")
            low_price = self._chart_price(item, "Low")
            close_price = self._chart_price(item, "Close")
            volume = self._number(item, "Volume", default=0.0)
            try:
                candles.append(
                    Candle(
                        timestamp=timestamp,
                        open=open_price,
                        high=high_price,
                        low=low_price,
                        close=close_price,
                        volume=volume,
                    )
                )
            except ValueError:
                continue
        return sorted(candles, key=lambda candle: candle.timestamp)

    async def portfolio_state(self) -> PortfolioState:
        positions_payload, balance = await asyncio.gather(self.positions(), self.balance())
        equity = self._number(
            balance,
            "TotalValue",
            "NetEquityForMargin",
            "AccountValue",
            default=0.0,
        )
        cash = self._number(balance, "CashBalance", "CashAvailableForTrading", default=0.0)
        if equity <= 0 or cash < 0:
            raise BrokerValidationError("Saxo portfolio response is missing balances")
        raw_positions = positions_payload.get("Data", positions_payload.get("data", []))
        positions = cast(list[object], raw_positions) if isinstance(raw_positions, list) else []
        gross_exposure = 0.0
        sector_exposure: dict[str, float] = {}
        for raw in positions:
            if not isinstance(raw, dict):
                continue
            item = cast(dict[str, object], raw)
            view_raw = item.get("PositionView", {})
            base_raw = item.get("PositionBase", {})
            view = cast(dict[str, object], view_raw) if isinstance(view_raw, dict) else {}
            base = cast(dict[str, object], base_raw) if isinstance(base_raw, dict) else {}
            exposure = abs(
                self._number(view, "Exposure", "MarketValue", default=0.0)
                or self._number(base, "Amount", default=0.0)
                * self._number(view, "CurrentPrice", default=0.0)
            )
            gross_exposure += exposure
            sector = str(item.get("Sector", "Unknown"))
            sector_exposure[sector] = sector_exposure.get(sector, 0.0) + exposure
        daily_pnl = self._number(
            balance, "ProfitLossOnTradePositions", "UnrealizedMarginProfitLoss", default=0.0
        )
        return PortfolioState(
            equity=equity,
            cash=cash,
            peak_equity=max(equity, self._number(balance, "HistoricalPeakEquity", default=equity)),
            daily_pnl=daily_pnl,
            open_positions=len(positions),
            gross_exposure=gross_exposure,
            sector_exposure=sector_exposure,
        )

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

    async def replace_protective_order(
        self,
        *,
        account_key: str,
        order_id: str,
        uic: int,
        asset_type: str,
        quantity: int,
        order_price: float,
        trailing_distance: float,
        trailing_step: float,
    ) -> dict[str, object]:
        if min(quantity, order_price, trailing_distance, trailing_step) <= 0:
            raise BrokerValidationError("invalid trailing stop request")
        return await self._request(
            "PATCH",
            "/trade/v2/orders",
            json={
                "AccountKey": account_key,
                "Amount": quantity,
                "AssetType": asset_type,
                "OrderDuration": {"DurationType": "GoodTillCancel"},
                "OrderId": order_id,
                "OrderPrice": order_price,
                "OrderType": "TrailingStop",
                "TrailingStopDistanceToMarket": trailing_distance,
                "TrailingStopStep": trailing_step,
                "Uic": uic,
            },
            retry_safe=False,
        )

    async def cancel_orders(
        self, *, account_key: str, order_ids: tuple[str, ...]
    ) -> dict[str, object]:
        if not order_ids or any(not item or "," in item or "/" in item for item in order_ids):
            raise BrokerValidationError("invalid order ids")
        return await self._request(
            "DELETE",
            f"/trade/v2/orders/{','.join(order_ids)}",
            params={"AccountKey": account_key},
            retry_safe=False,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
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
                    params=params,
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

    @staticmethod
    def _number(payload: dict[str, object], *keys: str, default: float = 0.0) -> float:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                return float(value)
        return default

    @classmethod
    def _chart_price(cls, payload: dict[str, object], prefix: str) -> float:
        direct = cls._number(payload, prefix, default=0.0)
        if direct > 0:
            return direct
        bid = cls._number(payload, f"{prefix}Bid", default=0.0)
        ask = cls._number(payload, f"{prefix}Ask", default=0.0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        return max(bid, ask)


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)
